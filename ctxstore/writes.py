"""The memory-tool verbs and the structured writes that create, move and
remove docs. Every one goes through the store's write path."""
import posixpath
import re

from . import frontmatter
from .contract import CtxError
from .store import decode
from .verbs import _budget, _clock, _fit, _one_line

LINK = re.compile(r"(\[[^\]\n]*\]\()([^)\s]+)(\))")
WIKI = re.compile(r"\[\[([^\]|#\n]+)([|#][^\]\n]*)?\]\]")


def _key(store, key):
    return store.key(key)


# --- view -------------------------------------------------------------------

def view(store, params):
    target = params.get("doc", "")
    folder = store.backend.folder(target)
    if folder is not None:
        prefix = folder + "/" if folder else ""
        keys = [key for key in store.keys() if key.startswith(prefix)]
        if not keys and folder:
            raise CtxError("NO_SUCH_DOC", target)
        lines = [f"{len(keys)} docs"] + [f"{key}.md ({len(store.read(key))} bytes)" for key in keys]
        kept, truncated = _fit(lines, _budget(params, 8192), "docs")
        return {"docs": keys, "truncated": truncated}, "\n".join(kept)
    doc = store.load(target)
    lines = doc.text.split("\n")
    if lines and not lines[-1]:
        lines.pop()
    first, last = 1, len(lines)
    if "range" in params:
        match = re.fullmatch(r"(\d+):(\d+)", params["range"])
        if not match or not 1 <= int(match.group(1)) <= int(match.group(2)):
            raise CtxError("USAGE", "--range")
        first, last = int(match.group(1)), min(int(match.group(2)), len(lines))
    width = len(str(last))
    numbered = [f"{number:>{width}}  {lines[number - 1]}" for number in range(first, last + 1)]
    kept, truncated = _fit(numbered, _budget(params, 8192))
    text = "\n".join(kept)
    return {"doc": doc.key, "lines": len(lines), "text": text, "truncated": truncated}, text


# --- create, new ------------------------------------------------------------

def _scaffold(store, name, key, title, date):
    text = store.backend.template(name)
    if text is None:
        text = "---\ntitle: {{TITLE}}\ntype: {{TYPE}}\nupdated: {{DATE}}\n---\n\n# {{TITLE}}\n"
    values = {"TITLE": title, "TYPE": name, "DATE": date, "KEY": key, "SLUG": key.rsplit("/", 1)[-1]}
    text = re.sub(r"\{\{([A-Z]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), text)
    stamp = store.stamp(name)
    return frontmatter.set_field(text, "schema_version", stamp) if stamp else text


def create(store, params):
    key = _key(store, params["doc"])
    now, date = _clock(params.get("now"))
    if "text" in params:
        text = params["text"]
    else:
        name = params.get("type")
        if not name:
            raise CtxError("USAGE", "text")
        text = _scaffold(store, name, key, _one_line(params.get("title", key.rsplit("/", 1)[-1]), "title"), date)
    if not text.endswith("\n"):
        text += "\n"
    row = store.write("create", key, lambda current: text, payload=text, now=now)
    return {"doc": row["doc"], "after": row["after"]}, f"created: {row['doc']}"


def new(store, params):
    key = _key(store, params["doc"])
    now, date = _clock(params.get("now"))
    title = _one_line(params.get("title", key.rsplit("/", 1)[-1]), "title")
    text = _scaffold(store, params["type"], key, title, date)

    def change(current):
        if current is not None:
            raise CtxError("DOC_EXISTS", key)
        return text
    row = store.write("new", key, change, payload=text, now=now)
    return {"doc": row["doc"], "after": row["after"]}, f"created: {row['doc']}"


# --- str_replace, insert ----------------------------------------------------

def str_replace(store, params):
    key, old, fresh = _key(store, params["doc"]), params["old"], params.get("new", "")
    now, _ = _clock(params.get("now"))
    if not old:
        raise CtxError("USAGE", "old")

    def change(current):
        if current is None:
            raise CtxError("NO_SUCH_DOC", key)
        count = current.count(old)
        if count == 0:
            raise CtxError("NO_MATCH", key)
        if count > 1:
            raise CtxError("AMBIGUOUS_SELECTOR", f"{key} ({count} matches)")
        return current.replace(old, fresh)
    row = store.write("str_replace", key, change, payload=fresh, now=now)
    return {"doc": row["doc"], "after": row["after"]}, f"replaced: {row['doc']}"


def insert(store, params):
    key, text = _key(store, params["doc"]), params["text"]
    now, _ = _clock(params.get("now"))

    def change(current):
        if current is None:
            raise CtxError("NO_SUCH_DOC", key)
        lines = current.split("\n")
        body = lines[:-1] if lines and not lines[-1] else lines
        if not 0 <= params["line"] <= len(body):
            raise CtxError("USAGE", "--line")
        body[params["line"]:params["line"]] = text.rstrip("\n").split("\n")
        return "\n".join(body) + "\n"
    row = store.write("insert", key, change, payload=text, now=now)
    return {"doc": row["doc"], "after": row["after"]}, f"inserted: {row['doc']}"


# --- delete, rename, move ---------------------------------------------------

def _target(source, link):
    """The doc key a markdown link in `source` points at, or None."""
    path = link.split("#", 1)[0]
    if not path.endswith(".md") or re.match(r"[a-z][a-z0-9+.-]*:", path) or path.startswith("/"):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), path))[:-3]


def _names(store, key):
    """What a wikilink may call the doc: its key, and its name when no other
    doc shares it."""
    name = key.rsplit("/", 1)[-1]
    shared = any(other != key and other.rsplit("/", 1)[-1] == name for other in store.keys())
    return {key} if shared else {key, name}


def _links(store, key):
    """Keys of the docs that link to `key`, by a relative markdown link or a
    wikilink of its key or its name."""
    names = _names(store, key)
    found = []
    for other in store.keys():
        if other == key:
            continue
        text = store.read(other, listed=True).decode("utf-8", errors="replace")
        hit = any(_target(other, m.group(2)) == key for m in LINK.finditer(text))
        hit = hit or any(m.group(1).strip() in names for m in WIKI.finditer(text))
        if hit:
            found.append(other)
    return found


def delete(store, params):
    key = _key(store, params["doc"])
    now, _ = _clock(params.get("now"))
    with store.locked():
        links = _links(store, key) if store.has(key) else []
        row = store.apply("delete", key, lambda current: None, now)
    text = f"deleted: {key}"
    if links:
        text += f" ({len(links)} docs still link to it: {', '.join(links)})"
    return {"doc": row["doc"], "links": links}, text


def _relink(text, source, old, fresh, names, moved_to=None):
    """`text` of the doc `source` with its links to `old` pointing at `fresh`.
    `names` is what a wikilink may call `old`; `moved_to` is the new key of
    the doc itself, when it is the one moving."""
    home = moved_to or source

    def markdown(match):
        target = _target(source, match.group(2))
        if target is None:
            return match.group(0)
        if target == old:
            target = fresh
        elif moved_to is None:
            return match.group(0)
        anchor = match.group(2)[len(match.group(2).split("#", 1)[0]):]
        path = posixpath.relpath(target + ".md", posixpath.dirname(home) or ".")
        return match.group(1) + path + anchor + match.group(3)

    def wiki(match):
        name = match.group(1).strip()
        if name not in names:
            return match.group(0)
        return "[[" + (fresh if "/" in name else fresh.rsplit("/", 1)[-1]) + (match.group(2) or "") + "]]"
    return WIKI.sub(wiki, LINK.sub(markdown, text))



def rename(store, params, verb="rename"):
    old, fresh = _key(store, params["doc"]), _key(store, params["to"])
    now, _ = _clock(params.get("now"))
    if old == fresh:
        raise CtxError("USAGE", "to")
    if store.generated(fresh):
        raise CtxError("GENERATED", fresh)
    with store.locked():
        if not store.has(old):
            raise CtxError("NO_SUCH_DOC", old)
        if store.has(fresh):
            raise CtxError("DOC_EXISTS", fresh)
        linking, names = _links(store, old), _names(store, old)
        moved = _relink(decode(store.read(old)), old, old, fresh, names, moved_to=fresh)
        store.apply(verb, fresh, lambda current: moved, now)
        store.apply(verb, old, lambda current: None, now)
        for other in linking:
            store.apply(verb, other, lambda current, other=other: _relink(current, other, old, fresh, names), now, check=False)
    text = f"{'moved' if verb == 'move' else 'renamed'}: {old} → {fresh}"
    if linking:
        text += f" ({len(linking)} docs relinked)"
    return {"doc": fresh, "from": old, "relinked": linking}, text


def move(store, params):
    return rename(store, params, verb="move")
