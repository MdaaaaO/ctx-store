"""Links between docs: relative markdown links and wikilinks."""
import posixpath
import re

LINK = re.compile(r"(\[[^\]\n]*\]\()([^)\s]+)(\))")
WIKI = re.compile(r"\[\[([^\]|#\n]+)([|#][^\]\n]*)?\]\]")


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


def inbound(store, key):
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


def outbound(store, doc):
    """Keys of the docs that `doc` links to and that exist, in the order they
    are first named."""
    keys, found = store.keys(), []
    present = set(keys)
    for match in re.finditer(LINK.pattern + "|" + WIKI.pattern, doc.text):
        if match.group(2) is not None:
            target = _target(doc.key, match.group(2))
        else:
            name = match.group(4).strip()
            named = [key for key in keys if key == name or key.rsplit("/", 1)[-1] == name]
            target = name if name in present else named[0] if len(named) == 1 else None
        if target in present and target != doc.key and target not in found:
            found.append(target)
    return found
