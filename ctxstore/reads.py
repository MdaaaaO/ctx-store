"""The read verbs: references and slices, not whole bodies. Reads run over
every store of the list, write nothing under a store and are not audited."""
import re

from . import frontmatter, fs, sections
from .contract import CtxError
from .store import HEAD, Doc, head_fields
from .verbs import FULL, _budget, _fit

SUMMARY = 80


def _holding(stores, key):
    for store in stores:
        if store.has(key):
            return store
    # the first store names the failure: PATH_ESCAPE or NO_SUCH_DOC
    stores[0].load(key)
    raise CtxError("NO_SUCH_DOC", key)


def _deliver(params, config, name, lines, what="lines", default=FULL):
    """The payload on stdout inside the budget, or whole in a scratch file."""
    out = params.get("out", "-")
    if out == "auto":
        if not config.scratch:
            raise CtxError("USAGE", "CTX_SCRATCH")
        data = ("\n".join(lines) + "\n").encode("utf-8")
        path = fs.write_scratch(config.scratch, name, data)
        return {"path": path, "bytes": len(data), "truncated": False}, path
    if out != "-":
        raise CtxError("USAGE", "--out")
    kept, truncated = _fit(lines, _budget(params, default), what)
    text = "\n".join(kept)
    return {"text": text, "bytes": len(text.encode("utf-8")) + 1, "truncated": truncated}, text


def get(stores, params, config):
    doc = _holding(stores, params["doc"]).load(params["doc"])
    if "section" in params:
        lines = sections.lines_of(doc.body, params["section"])
    else:
        lines = doc.body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if "tail" in params:
        if params["tail"] < 1:
            raise CtxError("USAGE", "--tail")
        kept = [line for _, line in sections.entries(lines)]
        lines = kept[-params["tail"]:]
    data, text = _deliver(params, config, doc.key.replace("/", "--") + ".md", lines)
    return {"doc": doc.key, **data}, text


def _summary(doc, needle):
    """≤ 80 characters: the first line that holds the query, else the first
    line of prose."""
    first = None
    for line in doc.body.split("\n"):
        plain = line.strip()
        if not plain or plain.startswith(("#", "<!--", "```", "|--", "---")):
            continue
        first = first or plain
        if needle and needle in plain.lower():
            first = plain
            break
    first = first or ""
    return first if len(first) <= SUMMARY else first[: SUMMARY - 1].rstrip() + "…"


def _row(prefix, doc, needle=""):
    cells = [
        prefix + doc.key,
        str(doc.fields.get("title") or doc.fields.get("session") or "-"),
        str(doc.fields.get("updated") or doc.fields.get("heartbeat") or "-"),
        _summary(doc, needle) or "-",
    ]
    return " · ".join(cells)


def _docs(stores):
    """(prefix, store, doc) for every readable doc; the prefix names the store
    when there is more than one."""
    seen = set()
    for number, store in enumerate(stores, 1):
        prefix = f"{number}:" if len(stores) > 1 else ""
        for key in store.keys():
            if key in seen:
                continue  # an earlier store of the list holds this key
            seen.add(key)
            try:
                doc = store.load(key, listed=True)
            except CtxError:
                continue  # a doc that does not parse is `validate`'s to report
            yield prefix, store, doc


def _head(data):
    """The bytes of a doc's frontmatter."""
    end = data.find(b"\n---", 3)
    return data if end < 0 else data[:end]


def find(stores, params, config):
    """Two passes: every doc is matched on its bytes, and only the hits that
    are shown are parsed into rows."""
    needle = params.get("query", "").lower()
    if not needle and "type" not in params and "tag" not in params:
        raise CtxError("USAGE", "query")
    plain = needle.isascii()
    wanted, hits, seen = needle.encode("utf-8"), [], set()
    for number, store in enumerate(stores, 1):
        prefix = f"{number}:" if len(stores) > 1 else ""
        for key in store.keys():
            if key in seen:
                continue  # an earlier store of the list holds this key
            seen.add(key)
            data = store.read(key, listed=True)
            in_key = needle in key.lower()
            if plain:  # bytes.lower() folds ASCII only, which is all an ASCII query needs
                in_doc, in_head = wanted in data.lower(), wanted in _head(data).lower()
            else:
                try:
                    text = data.decode("utf-8").lower()
                except UnicodeDecodeError:
                    continue
                in_doc, in_head = needle in text, needle in _head(data).decode("utf-8", errors="replace").lower()
            if not (in_key or in_doc):
                continue
            if "type" in params or "tag" in params:
                fields = head_fields(data[:HEAD])
                if fields is None:  # frontmatter longer than the head, or broken
                    try:
                        fields = Doc(key, data).fields
                    except CtxError:
                        continue
                if "type" in params and store.type_by(key, fields) != params["type"]:
                    continue
                tags = fields.get("tags")
                if "tag" in params and params["tag"] not in (tags if isinstance(tags, list) else [tags]):
                    continue
            hits.append((0 if in_key or in_head else 1, key, prefix, data))
    hits.sort(key=lambda hit: hit[:2])
    limit = None if params.get("out") == "auto" else _budget(params, 4096)
    rows, used = [], 0
    for _, key, prefix, data in hits:
        if limit is not None and used > limit and head_fields(data[:HEAD]) is not None:
            rows.append("")  # past the budget: counted, never shown; its head parses, the body is left alone
            continue
        try:
            rows.append(_row(prefix, Doc(key, data), needle))
        except CtxError:
            continue  # a doc that does not parse is `validate`'s to report
        used += len(rows[-1].encode("utf-8")) + 1
    data, text = _deliver(params, config, "find.txt", [f"{len(rows)} hits"] + rows, "hits", default=4096)
    return {"hits": len(rows), **data}, text


def _naming(stores, key):
    """(prefix, store, doc) of the docs whose bytes hold the key at all."""
    seen, wanted = set(), key.encode("utf-8")
    for number, store in enumerate(stores, 1):
        prefix = f"{number}:" if len(stores) > 1 else ""
        for name in store.keys():
            if name in seen:
                continue
            seen.add(name)
            data = store.read(name, listed=True)
            if wanted in data:
                try:
                    yield prefix, store, Doc(name, data)
                except CtxError:
                    continue


def resolve(stores, params, config):
    key = params["key"]
    found = []
    for prefix, store, doc in _naming(stores, key):
        settings = store.marker["resolve"]
        shape = settings.get("key_regex")
        if not shape:
            continue
        try:
            if not re.fullmatch(shape, key):
                continue
        except re.error:
            raise CtxError("SCHEMA_VIOLATION", "resolve.key_regex") from None
        token = re.compile(r"(?<![A-Za-z0-9_#-])" + re.escape(key) + r"(?![A-Za-z0-9_-])")
        values = []
        for field in settings.get("fields", []):
            value = doc.fields.get(field)
            values += value if isinstance(value, list) else [value]
        where = None
        if key in values:
            where = 0
        elif settings.get("section") in sections.names(doc.body) and settings["section"] not in sections.duplicates(doc.body):
            if token.search("\n".join(sections.lines_of(doc.body, settings["section"]))):
                where = 1
        if where is not None:
            found.append((where, doc.key, prefix, doc))
    if not any(store.marker["resolve"].get("key_regex") for store in stores):
        raise CtxError("USAGE", "resolve.key_regex")
    found.sort(key=lambda hit: hit[:2])
    best = [hit for hit in found if hit[0] == found[0][0]] if found else []
    if not best:
        raise CtxError("NO_SUCH_DOC", key)
    if len(best) > 1:
        raise CtxError("AMBIGUOUS_SELECTOR", f"{key} ({', '.join(p + k for _, k, p, _ in best)})")
    _, doc_key, prefix, doc = best[0]
    row = _row(prefix, doc)
    return {"doc": doc_key, "row": row}, row
