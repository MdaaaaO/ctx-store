"""Sections: a section is an exact `##` heading and the lines below it, up
to the next heading of the same or a higher level. Fenced code is skipped."""
import re

from .contract import CtxError

HEADING = re.compile(r"^(#{1,6}) +(.*?)\s*$")
FENCE = re.compile(r"^(```|~~~)")


def headings(body):
    """(line index, level, text) for every heading outside fenced code."""
    found, fence = [], None
    for index, line in enumerate(body.split("\n")):
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
