"""`ctx help` prints from interface.md, so the spec and the help cannot drift."""
import re

from . import fs

SPEC = "interface.md"


def _sections(text, level):
    """Ordered (heading, body) pairs for headings of exactly this level."""
    marker = "#" * level + " "
    found, heading, body, fenced = [], None, [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        starts = not fenced and re.match(r"#{1,%d} " % level, line)
        if starts:
            if heading is not None:
                found.append((heading, "\n".join(body).strip()))
            heading = line[len(marker):].strip() if line.startswith(marker) else None
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        found.append((heading, "\n".join(body).strip()))
    return found


def slug(heading):
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def verbs():
    """Verb sections: `### <verb>` headings in the spec."""
    return dict(_sections(fs.package_text(SPEC), 3))


def topics():
    """Topic sections: `## <Heading>`, addressed by slug. Sub-sections stay out."""
    return {slug(h): (h, b) for h, b in _sections(fs.package_text(SPEC), 2)}


def overview():
    text = fs.package_text(SPEC)
    return text.split("\n## ", 1)[0].split("\n", 1)[1].strip()
