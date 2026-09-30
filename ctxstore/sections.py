"""Sections: a section is an exact `##` heading and the lines below it, up
to the next heading of the same or a higher level. Fenced code is skipped."""
import re

from .contract import CtxError

HEADING = re.compile(r"^(#{1,6}) +(.*?)\s*$")
DATED = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b")
FENCE = re.compile(r"^(```|~~~)")


def headings(body):
    """(line index, level, text) for every heading outside fenced code."""
    found, fence = [], None
    for index, line in enumerate(body.split("\n")):
        if line[:1] not in "#`~":
            continue
        mark = FENCE.match(line)
        if mark:
            if fence is None:
                fence = mark.group(1)
            elif line.startswith(fence):
                fence = None
            continue
        if fence is None:
            match = HEADING.match(line)
            if match:
                found.append((index, len(match.group(1)), match.group(2)))
    return found


def names(body):
    return [text for _, level, text in headings(body) if level == 2]


def duplicates(body):
    seen, twice = set(), []
    for name in names(body):
        if name in seen and name not in twice:
            twice.append(name)
        seen.add(name)
    return twice


def span(body, name):
    """(first line after the heading, line after the section's last line)."""
    found = headings(body)
    hits = [i for i, (_, level, text) in enumerate(found) if level == 2 and text == name]
    if not hits:
        raise CtxError("NO_SUCH_SECTION", name)
    if len(hits) > 1:
        raise CtxError("AMBIGUOUS_SELECTOR", name)
    start = found[hits[0]][0] + 1
    end = len(body.split("\n"))
    for index, level, _ in found[hits[0] + 1:]:
        if level <= 2:
            end = index
            break
    return start, end


def lines_of(body, name):
    start, end = span(body, name)
    return body.split("\n")[start:end]


def rename(body, old, new):
    """The body with the `##` heading `old` called `new`; unchanged without it."""
    if old not in names(body) or new in names(body):
        return body
    lines = body.split("\n")
    for index, level, text in headings(body):
        if level == 2 and text == old:
            lines[index] = "## " + new
    return "\n".join(lines)


def order_entries(body, name, order, source=None):
    """The body with the entries of one section in `order`, judged by the
    dates they start with. Entries that read the other way round are
    reversed; entries already in order, or in no order, are left alone.
    With the `source` order declared, entries whose dates tie also read that
    way round: a log of one busy day is reversed too. Blank lines and
    comments stay where they are."""
    if name not in names(body) or name in duplicates(body):
        return body
    start, end = span(body, name)
    lines = body.split("\n")
    found = [start + number - 1 for number, _ in entries(lines[start:end])]
    dates = [match.group(1) for match in (DATED.match(lines[index]) for index in found) if match]
    wanted = sorted(dates, reverse=order == "newest-first")
    if len(dates) != len(found) or dates != wanted[::-1] or dates == wanted and not source:
        return body
    for index, line in zip(found, [lines[i] for i in reversed(found)]):
        lines[index] = line
    return "\n".join(lines)


def replace_in_comments(body, old, new):
    """The body with `old` replaced inside HTML comments only: prose is never
    rewritten."""
    out, comment = [], False
    for line in body.split("\n"):
        if comment or line.strip().startswith("<!--"):
            comment = "-->" not in line
            line = line.replace(old, new)
        out.append(line)
    return "\n".join(out)


def entries(lines):
    """(line number, line) of the lines that are neither blank nor inside an
    HTML comment."""
    comment = False
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if comment or text.startswith("<!--"):
            comment = "-->" not in text
        elif text:
            yield number, line


def title_span(body):
    """(first line after the `#` title, the first `##` heading after it, or
    the end of the body): the region a sectionless log lives in, for a type
    whose `log` declares no `section`."""
    found = headings(body)
    titles = [i for i, (_, level, _) in enumerate(found) if level == 1]
    start = found[titles[0]][0] + 1 if titles else 0
    end = len(body.split("\n"))
    for index, level, _ in found:
        if level >= 2 and index >= start:
            end = index
            break
    return start, end


def body_entries(body):
    """Absolute line indices of the body-level dated list: the `- <date> —
    …` lines after the `#` title and before the first `##` heading. Prose
    and HTML comments around them are skipped, and never moved."""
    start, end = title_span(body)
    lines = body.split("\n")
    return [start + number - 1 for number, line in entries(lines[start:end]) if DATED.match(line)]


def body_prepend_line(body, line):
    """The body with one line added before the body-level list's first
    entry. A list with no entries takes it as its last line."""
    found = body_entries(body)
    if not found:
        return body_append_line(body, line)
    lines = body.split("\n")
    lines[found[0]:found[0]] = [line]
    return "\n".join(lines)


def body_append_line(body, line):
    """The body with one line added after the title region's last
    non-blank line: the body-level list's last entry, once it has one."""
    start, end = title_span(body)
    lines = body.split("\n")
    last = end
    while last > start and not lines[last - 1].strip():
        last -= 1
    lines[last:last] = [line]
    if end == len(body.split("\n")) and last == end:
        lines.append("")  # the title region ran to the end of a file without a final newline
    return "\n".join(lines)


def prepend_line(body, name, line):
    """The body with one line added before the section's first entry: the
    first line that is neither blank nor a comment. An empty section takes it
    as its last line."""
    start, end = span(body, name)
    lines = body.split("\n")
    for number, _ in entries(lines[start:end]):
        index = start + number - 1
        lines[index:index] = [line]
        return "\n".join(lines)
    return append_line(body, name, line)


def append_line(body, name, line):
    """The body with one line added after the section's last non-blank line."""
    start, end = span(body, name)
    lines = body.split("\n")
    last = end
    while last > start and not lines[last - 1].strip():
        last -= 1
    lines[last:last] = [line]
    if end == len(body.split("\n")) and last == end:
        lines.append("")  # the section ran to the end of a file without a final newline
    return "\n".join(lines)
