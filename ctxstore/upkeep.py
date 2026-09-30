"""`migrate` and `maintain`: the store changes its own format and keeps
itself tidy, without a model's turns."""
import calendar
import difflib
import time

from . import frontmatter, sections
from .contract import CtxError, Findings
from .verbs import SIZE_GUARD, _clock

KEEP_LOG = 20
SESSION_DAYS = 7
GIT_DEBOUNCE = 300


# --- migrate ----------------------------------------------------------------

def _migrated(store, doc, name):
    """The doc's text after every step it is behind, and the steps' numbers."""
    schema = store.types[name]
    at = store.version_of(doc, name)
    text, log = doc.text, schema.get("log", {}).get("section")
    steps = [step for step in schema.get("migrations", []) if step["to"] > at]
    for step in steps:
        for old, new in step.get("rename_fields", {}).items():
            text = frontmatter.rename_field(text, old, new)
        for field in step.get("remove_fields", []):
            text = frontmatter.remove_field(text, field)
        for field, value in step.get("set_fields", {}).items():
            text = frontmatter.set_field(text, field, value)
        head = text[: len(text) - len(frontmatter.split(text)[1])]
        body = frontmatter.split(text)[1]
        for old, new in step.get("rename_sections", {}).items():
            body = sections.rename(body, old, new)
            log = new if log == old else log
        if "log_order" in step and log:
            body = sections.order_entries(body, log, step["log_order"], step.get("log_order_from"))
        for pair in step.get("replace_comments", []):
            body = sections.replace_in_comments(body, pair["old"], pair["new"])
        text = head + body
    text = frontmatter.set_field(text, "schema_version", store.stamp(name))
    return text, [step["to"] for step in steps]


def _pending(store):
    """(doc, type name, version it is at) of every doc behind its type."""
    found = []
    for key in store.keys():
        try:
            doc = store.load(key, listed=True)
        except CtxError:
            continue  # a doc that does not parse is `validate`'s to report
        name = store.type_of(doc)
        version = store.types.get(name or "", {}).get("version", 0)
        at = store.version_of(doc, name) if name else None
        if at is not None and at < version:
            found.append((doc, name, at))
    return found


def migrate(store, params, config):
    modes = [mode for mode in ("check", "dry-run", "apply") if params.get(mode)]
    if len(modes) != 1:
        raise CtxError("USAGE", "migrate")
    now, _ = _clock(params.get("now"))
    pending = _pending(store)
    plan = []
    for doc, name, at in pending:
        text, steps = _migrated(store, doc, name)
        delta = difflib.unified_diff(doc.text.split("\n"), text.split("\n"), lineterm="", n=0)
        changed = sum(1 for line in delta if line[:1] in "+-" and line[:3] not in ("+++", "---"))
        plan.append({"doc": doc.key, "type": name, "from": at, "to": store.types[name]["version"],
                     "steps": steps, "lines": changed, "text": text})
    rows = [f"{p['doc']}: {p['type']} v{p['from']} → v{p['to']} (steps {', '.join(map(str, p['steps'])) or '-'}; "
            f"{p['lines']} lines added or removed)" for p in plan]
    public = [{key: value for key, value in p.items() if key != "text"} for p in plan]
    if modes[0] == "check":
        if plan:
            raise Findings([("MIGRATION_PENDING", p["doc"], p["doc"]) for p in plan])
        return {"pending": []}, "ok: nothing to migrate"
    if modes[0] == "dry-run":
        return {"pending": public}, "\n".join([f"{len(plan)} docs to migrate"] + rows)
    if plan and not store.named:
        raise CtxError("STORE_NOT_NAMED")
    # Every doc is checked at its new version before any is written: a doc that
    # would not be valid is named, as `validate` names it, and the run writes nothing.
    blocked = [(code, detail, p["doc"]) for p in plan
               for code, detail in store.findings(p["doc"], p["text"].encode("utf-8"))]
    if blocked:
        raise Findings(blocked)
    for p in plan:
        store.write("migrate", p["doc"], lambda current, p=p: p["text"], now=now, actor=_owner(store, p["doc"], config))
    return {"migrated": public}, "\n".join([f"{len(plan)} docs migrated"] + rows)


def _owner(store, key, config):
    """Who a housekeeping write is recorded for: the doc's owner when its type
    names one, else the actor."""
    doc = store.load(key)
    field = store.types.get(store.type_of(doc) or "", {}).get("owner")
    owner = doc.fields.get(field) if field else None
    return owner if isinstance(owner, str) and owner else config.actor


# --- maintain ---------------------------------------------------------------

def _seconds(stamp):
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def _archive_logs(store, rules, config, now, date, report):
    guard, keep = rules.get("size_guard", SIZE_GUARD), rules.get("keep_log", KEEP_LOG)
    pattern = rules.get("archive", "archive/{slug}-log")
    for key in store.keys():
        if len(store.read(key, listed=True)) <= guard:
            continue
        doc = store.load(key)
        log = store.types.get(store.type_of(doc) or "", {}).get("log", {})
        name = log.get("section")
        if not name or name not in sections.names(doc.body) or name in sections.duplicates(doc.body):
            continue
        start, _ = sections.span(doc.body, name)
        lines = doc.body.split("\n")
        found = [start + number - 1 for number, _ in sections.entries(sections.lines_of(doc.body, name))]
        newest_first = log.get("order") == "newest-first"
        old = found[keep:] if newest_first else found[: max(len(found) - keep, 0)]
        if not old:
            continue
        moved = [lines[index] for index in old]
        if newest_first:
            moved.reverse()
        kept = "\n".join(line for index, line in enumerate(lines) if index not in set(old))
        head = doc.text[: len(doc.text) - len(doc.body)]
        target = store.key(pattern.format(slug=key.rsplit("/", 1)[-1], key=key))
        archive_type = store.type_of(store.load(target)) if store.has(target) else None
        archive_log = store.types.get(archive_type or "log", {}).get("log")
        section = "Log" if archive_log is None else archive_log.get("section")
        archive_newest = archive_log is not None and archive_log.get("order") == "newest-first"

        def extend(current, moved=moved, key=key, section=section, archive_newest=archive_newest):
            if current is None:
                title = f"# Log of {key}\n"
                current = (f"---\ntitle: Log of {key}\ntype: log\nupdated: {date}\n---\n\n"
                           + (title + "\n## " + section + "\n" if section else title))
                if store.stamp("log"):
                    current = frontmatter.set_field(current, "schema_version", store.stamp("log"))
            body = frontmatter.split(current)[1]
            for line in moved:
                if section and archive_newest:
                    body = sections.prepend_line(body, section, line)
                elif section:
                    body = sections.append_line(body, section, line)
                elif archive_newest:
                    body = sections.body_prepend_line(body, line)
                else:
                    body = sections.body_append_line(body, line)
            return current[: len(current) - len(frontmatter.split(current)[1])] + body
        with store.locked():
            store.apply("maintain", target, extend, now, config.actor)
            store.apply("maintain", key, lambda current, text=head + kept: text, now, _owner(store, key, config))
        report.append(f"archived: {len(moved)} log entries of {key} → {target}")


def _sweep_sessions(store, rules, config, now, report):
    from . import writes
    days = rules.get("session_days", SESSION_DAYS)
    pattern = rules.get("session_archive", "sessions/archive/{name}")
    for doc in store.of_type("session"):
        name, beat = doc.fields.get("session"), doc.fields.get("heartbeat")
        target = pattern.format(name=name, key=doc.key)
        if doc.fields.get("status") != "ended" or not isinstance(name, str) or doc.key == target:
            continue
        if doc.key.startswith(target.rsplit("/", 1)[0] + "/"):
            continue  # already in the archive
        try:
            age = _seconds(now) - _seconds(beat)
        except (TypeError, ValueError):
            continue
        if age < days * 86400:
            continue
        actor, config.actor = config.actor, name
        try:
            writes.rename(store, {"doc": doc.key, "to": target, "now": now}, verb="maintain")
        finally:
            config.actor = actor
        with store.locked():
            store.backend.audit_archive(name)
        report.append(f"swept: {doc.key} → {target}")


def _catalog(store, rules, report):
    key = rules.get("catalog")
    if not key:
        return
    key = store.key(key)
    if not store.generated(key):
        raise CtxError("SCHEMA_VIOLATION", "maintain.catalog")
    rows = ["# Catalog", "", "Generated by `ctx maintain`; do not edit.", "",
            "| Doc | Title | Type | Status | Updated |", "|---|---|---|---|---|"]
    for other in store.keys():
        try:
            doc = store.load(other, listed=True)
        except CtxError:
            continue
        cells = [f"[`{other}`]({other}.md)", doc.fields.get("title") or doc.fields.get("session") or "-",
                 store.type_of(doc) or "-", doc.fields.get("status") or "-",
                 doc.fields.get("updated") or doc.fields.get("heartbeat") or "-"]
        rows.append("| " + " | ".join(str(cell).replace("|", "\\|") for cell in cells) + " |")
    data = ("\n".join(rows) + "\n").encode("utf-8")
    if store.backend.exists(key) and store.backend.read(key) == data:
        return
    with store.locked():
        store.backend.write(key, data)
    report.append(f"catalog: {key} ({len(rows) - 6} docs)")


def maintain(store, params, config):
    if not store.named:
        raise CtxError("STORE_NOT_NAMED")
    if store.backend.read_only():
        raise CtxError("STORE_READONLY", store.locator)
    now, date = _clock(params.get("now"))
    rules = store.marker["maintain"]
    report = []
    _archive_logs(store, rules, config, now, date, report)
    _sweep_sessions(store, rules, config, now, report)
    _catalog(store, rules, report)
    if config.git:
        done = store.backend.commit(config.actor, "maintain", rules.get("git_debounce", GIT_DEBOUNCE), _seconds(now))
        if done:
            report.append(done)
    return {"done": report}, "\n".join(report) if report else "ok: nothing to do"
