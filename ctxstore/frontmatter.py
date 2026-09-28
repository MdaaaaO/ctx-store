"""Frontmatter: the flat subset of YAML a doc may use.

One `key: value` per line; a value is a scalar or an inline list `[a, b]`.
Edits change one line and leave every other byte of the doc alone.
"""
import re

from .contract import CtxError

FENCE = "---"
KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?: (.*)|)$")


def split(text):
    """(frontmatter lines, body) — the lines between the first two `---`.
    Only the frontmatter is cut into lines; the body is one slice."""
    end = text.find("\n")
    if end < 0 or text[:end].rstrip() != FENCE:
        raise CtxError("SCHEMA_VIOLATION", "frontmatter")
    lines = []
    while True:
        start = end + 1
        end = text.find("\n", start)
        line = text[start:] if end < 0 else text[start:end]
        if line.rstrip() == FENCE:
            return lines, "" if end < 0 else text[end + 1:]
        if end < 0:
            raise CtxError("SCHEMA_VIOLATION", "frontmatter")
        lines.append(line)


def _scalar(raw):
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


def _value(raw):
    raw = (raw or "").strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            return None
        inner = raw[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if raw[:1] in ("{", "|", ">", "&", "*"):
        return None
    return _scalar(raw)


def parse(lines):
    """Ordered dict of the fields; anything outside the subset is a violation
    named by its key (or `frontmatter` when the line has none)."""
    fields = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = KEY.match(line)
        if not match:
            raise CtxError("SCHEMA_VIOLATION", "frontmatter")
        key, value = match.group(1), _value(match.group(2))
        if value is None or key in fields:
            raise CtxError("SCHEMA_VIOLATION", key)
        fields[key] = value
    return fields


def render(value):
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]"
    return value


def one_line(key, value):
    parts = value if isinstance(value, list) else [value]
    for part in parts:
        if "\n" in part or "\r" in part:
            raise CtxError("SCHEMA_VIOLATION", key)
        if isinstance(value, list) and ("," in part or "]" in part or not part.strip()):
            raise CtxError("SCHEMA_VIOLATION", key)


def rename_field(text, old, new):
    """The doc with a field renamed in place; unchanged without the field."""
    lines, body = split(text)
    if new in parse(lines):
        return text
    for index, line in enumerate(lines):
        match = KEY.match(line)
        if match and match.group(1) == old:
            lines[index] = new + line[len(old):]
    return "\n".join([FENCE, *lines, FENCE]) + "\n" + body


def remove_field(text, key):
    lines, body = split(text)
    kept = [line for line in lines if not (KEY.match(line) and KEY.match(line).group(1) == key)]
    return "\n".join([FENCE, *kept, FENCE]) + "\n" + body


def set_field(text, key, value):
    """The doc with one field set: its line replaced in place, or added as
    the last line of the frontmatter."""
    one_line(key, value)
    lines, body = split(text)
    parse(lines)
    new = f"{key}: {render(value)}".rstrip()
    for index, line in enumerate(lines):
        match = KEY.match(line)
        if match and match.group(1) == key:
            lines[index] = new
            break
    else:
        lines.append(new)
    return "\n".join([FENCE, *lines, FENCE]) + "\n" + body
