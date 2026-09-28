"""The store verbs. Each takes the store and its parameters and returns
(data for the envelope, text for a terminal)."""
import re

from . import clock, frontmatter, sections
from .contract import CtxError, Findings
from .store import DATE, TIMESTAMP, digest

SIZE_GUARD = 30000
BUDGET = 4096
FULL = 8192
TAIL = 5


def _clock(now):
    """(timestamp, date) of this run: `--now` when given, the clock otherwise."""
    if now is None:
        return clock.now_utc(), clock.today()
    if not TIMESTAMP.match(now):
        raise CtxError("USAGE", "--now")
    return now, now[:10]


def _one_line(text, name):
    text = text.rstrip("\n")
    if not text.strip() or "\n" in text or "\r" in text:
        raise CtxError("SCHEMA_VIOLATION", name)
    return text


# --- validate ---------------------------------------------------------------

def validate(store, params):
    changed, adopt = params.get("changed", False), params.get("adopt", False)
    if adopt and not changed:
        raise CtxError("USAGE", "--adopt")
    if adopt and not store.named:
        raise CtxError("STORE_NOT_NAMED")
    now, _ = _clock(params.get("now"))
    audited, broken = store.audited() if changed else ({}, set())
    found, checked, adopted, warnings = [], 0, [], []
    guard = store.marker.get("maintain", {}).get("size_guard", SIZE_GUARD)
    for key in store.keys():
        data = store.read(key, listed=True)
        if len(data) > guard:
            warnings.append({"code": "SIZE_GUARD", "doc": key, "bytes": len(data)})
        if changed and digest(data) in audited.get(key, ()) and key not in broken:
            continue
        checked += 1
        problems = store.findings(key, data)
        found += [(code, detail, key) for code, detail in problems]
        if changed and not problems and adopt:
            store.adopt(key, data, now)
            adopted.append(key)
        elif changed and not problems:
            found.append(("UNAUDITED_WRITE", key, key))
    if changed:
        present = set(store.keys())
        for key in sorted(audited):
            if key not in present and key not in store.removed and not store.generated(key):
                found.append(("UNAUDITED_WRITE", key, key))
    if found:
        raise Findings(found)
    data = {"checked": checked, "adopted": adopted, "warnings": warnings}
    text = f"ok: {checked} docs checked"
    if adopt:
        text += f", {len(adopted)} adopted"
    for warning in warnings:
        text += f"\nwarning: SIZE_GUARD {warning['doc']}: {warning['bytes']} bytes"
    return data, text


# --- log --------------------------------------------------------------------

def log(store, params):
    key, text = params["doc"], _one_line(params["text"], "text")
    now, date = _clock(params.get("now"))
    if "date" in params:
        if not DATE.match(params["date"]):
            raise CtxError("USAGE", "--date")
        date = params["date"]
    doc = store.load(store.target(key))
    schema = store.types.get(store.type_of(doc) or "", {}).get("log", {})
    if schema.get("ledger") and "section" not in params:
        line = text

        def change(current):
            return current + ("" if current.endswith("\n") else "\n") + line + "\n"
    else:
        name = params["section"] if "section" in params else schema.get("section")
        if not name:
            raise CtxError("USAGE", "--section")
        line = f"- {date} — {text}"
        newest = schema.get("order") == "newest-first"
        add = sections.prepend_line if newest else sections.append_line

        def change(current):
            head, body = current[: len(current) - len(_body(current))], _body(current)
            return head + add(body, name, line)
    row = store.write("log", key, change, payload=text, now=now)
    return {"doc": row["doc"], "line": line, "after": row["after"]}, f"logged: {row['doc']}"


def _body(text):
    return frontmatter.split(text)[1]


# --- fm ---------------------------------------------------------------------

def fm(store, params):
    key, field, raw = params["doc"], params["field"], params["value"]
    if not frontmatter.KEY.match(f"{field}: x"):
        raise CtxError("USAGE", field)
    now, date = _clock(params.get("now"))
    doc = store.load(store.target(key))
    rules = store.types.get(store.type_of(doc) or "", {}).get("frontmatter", {})
    value = raw
    if rules.get(field, {}).get("kind") == "list" or isinstance(doc.fields.get(field), list):
        value = [part.strip() for part in re.split(r",", raw.strip().strip("[]")) if part.strip()]

    def change(current):
        text = frontmatter.set_field(current, field, value)
        if "updated" in rules and field != "updated":
            text = frontmatter.set_field(text, "updated", date)
        stamp = store.stamp(store.type_of(doc) or "")
        if stamp and field != "schema_version":
            text = frontmatter.set_field(text, "schema_version", stamp)
        return text
    row = store.write("fm", key, change, payload=raw, now=now, versioned=field != "schema_version")
    return {"doc": row["doc"], "field": field, "value": value, "after": row["after"]}, f"set: {row['doc']} {field}"


# --- sessions ---------------------------------------------------------------

def _session(store, ident):
    hits = [
        doc for doc in store.of_type("session")
        if ident in (doc.fields.get("session_id"), doc.fields.get("session"))
    ]
    if not hits:
        raise CtxError("NO_SUCH_DOC", ident)
    if len(hits) > 1:
        raise CtxError("AMBIGUOUS_SELECTOR", ident)
    return hits[0]


def touch(store, params):
    now, _ = _clock(params.get("now"))
    if not store.named:
        raise CtxError("STORE_NOT_NAMED")
    doc = _session(store, params["session"])
    working = _one_line(params["working"], "working") if "working" in params else None

    def change(current):
        text = frontmatter.set_field(current, "heartbeat", now)
        return text if working is None else frontmatter.set_field(text, "working_on", working)
    owner = doc.fields.get("session") or None
    row = store.write("touch", doc.key, change, payload=working or "", now=now, actor=owner)
    return {"doc": row["doc"], "heartbeat": now, "after": row["after"]}, f"touched: {row['doc']}"


# --- brief ------------------------------------------------------------------

def _budget(params, default=BUDGET):
    budget = params.get("budget", default)
    if budget < 1:
        raise CtxError("USAGE", "--budget")
    return budget if params.get("full") else min(budget, FULL)


def _fit(lines, budget, what="lines"):
    """The lines that fit the byte budget, a marker line when some do not."""
    kept, used = [], 0
    for index, line in enumerate(lines):
        size = len(line.encode("utf-8")) + 1
        marker = f"… {len(lines) - index} more {what}, raise --budget".encode("utf-8")
        if used + size + (len(marker) + 1 if index < len(lines) - 1 else 0) > budget:
            rest = len(lines) - index
            note = f"… {rest} more {what}, raise --budget"
            while kept and used + len(note.encode("utf-8")) + 1 > budget:
                used -= len(kept.pop().encode("utf-8")) + 1
                rest += 1
                note = f"… {rest} more {what}, raise --budget"
            return kept + [note], True
        kept.append(line)
        used += size
    return kept, False


def _doc_lines(store, doc, body=False):
    name = store.type_of(doc)
    lines = [f"{doc.key} ({name or 'untyped'}, {len(doc.data)} bytes)"]
    for field, value in doc.fields.items():
        if field != "type" and value not in ("", []):
            lines.append(f"{field}: {frontmatter.render(value)}")
    spans = []
    for heading in sections.names(doc.body):
        if heading not in sections.duplicates(doc.body):
            size = len("\n".join(sections.lines_of(doc.body, heading)).encode("utf-8"))
            spans.append(f"{heading} ({size})")
    if spans:
        lines.append("sections: " + " · ".join(spans))
    log_rules = store.types.get(name or "", {}).get("log", {})
    section = log_rules.get("section")
    if section in sections.names(doc.body) and section not in sections.duplicates(doc.body):
        entries = [line for _, line in sections.entries(sections.lines_of(doc.body, section))]
        if entries:
            lines.append(f"{section} (last {min(TAIL, len(entries))} of {len(entries)}):")
            lines += entries[:TAIL] if log_rules.get("order") == "newest-first" else entries[-TAIL:]
    if body:
        lines.append("")
        lines += doc.body.strip("\n").split("\n")
    return lines


def _registry_lines(store):
    docs = [d for d in store.of_type("session") if d.fields.get("status") != "ended"]
    docs.sort(key=lambda d: str(d.fields.get("session") or d.key))
    lines = [f"sessions: {len(docs)} not ended"]
    for doc in docs:
        cells = [str(doc.fields.get(f) or "-") for f in ("session", "status", "epic", "working_on", "heartbeat")]
        lines.append("- " + " · ".join(cells))
    return lines


def brief(store, params):
    chosen = [name for name in ("registry", "session", "doc") if params.get(name)]
    if len(chosen) != 1:
        raise CtxError("USAGE", "brief")
    if chosen[0] == "registry":
        lines = _registry_lines(store)
    elif chosen[0] == "session":
        lines = _doc_lines(store, _session(store, params["session"]), body=True)
    else:
        lines = _doc_lines(store, store.load(params["doc"]))
    kept, truncated = _fit(lines, _budget(params))
    text = "\n".join(kept)
    return {"text": text, "bytes": len(text.encode("utf-8")) + 1, "truncated": truncated}, text
