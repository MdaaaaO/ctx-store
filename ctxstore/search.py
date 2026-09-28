"""Matching and ranking for `find`: a scan, no index.

A query is terms. A doc is a hit when every term occurs in it; hits are
ranked by where the terms occur and how rare they are. Matching folds case,
ignores a plural `s` on a term and the `-` and `_` inside words, so
`rollout` finds `roll-out` and `orders` finds `Order model`.
"""
import math
import re

from . import frontmatter, sections

TERM = re.compile(r'"([^"]+)"|(\S+)')
HEADING = re.compile(r"^#{1,6} +(.*?)\s*$")
JOINERS = str.maketrans("", "", "-_")
KEY, TITLE, TAGS, HEAD, FIELD, BODY = 5.0, 5.0, 3.0, 3.0, 1.0, 1.0
PHRASE, TOGETHER = 4.0, 2.0
SCORED = 300  # hits scored in full; past that, the order is key and frontmatter first, then key


def terms(query):
    """The terms of a query, folded: words, and phrases in double quotes."""
    found = []
    for phrase, word in TERM.findall(query.lower()):
        term = phrase.strip() or word.strip('"')
        if not phrase and len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
            term = term[:-1]
        if term and term not in found:
            found.append(term)
    return found


def squeeze(text):
    return text.translate(JOINERS)


def holds(term, text, squeezed):
    return term in text or squeeze(term) in squeezed


def held(found, wanted, key, data, holders):
    """Whether the doc holds every term, in its key or in its bytes; adds the
    doc to `holders` for each term it holds. ASCII terms are answered by the
    bytes; another term has the text decoded. Called once per doc of the
    store, so it does nothing it does not need."""
    lowered = data.lower()
    low = squeezed = text = tight = None
    every = True
    for index, term in enumerate(found):
        plain, narrow = wanted[index]
        if plain is not None and plain in lowered:
            holders[index] += 1
            continue
        if low is None:
            low = key.lower()
        there = term in low or squeeze(term) in squeeze(low)
        if not there and plain is not None:
            if squeezed is None:
                squeezed = lowered.replace(b"-", b"").replace(b"_", b"")
            there = narrow in squeezed
        elif not there:
            if text is None:
                try:
                    text = data.decode("utf-8").lower()
                except UnicodeDecodeError:
                    text = ""
                tight = squeeze(text)
            there = term in text or squeeze(term) in tight
        if there:
            holders[index] += 1
        else:
            every = False
    return every


def prepare(found):
    return [(t.encode() if t.isascii() else None, squeeze(t).encode() if t.isascii() else None) for t in found]


def headings(body):
    """The text of every heading line. Headings are few and the body is
    long, so this looks for `#` at the start of a line and nothing else."""
    found = []
    at = 0 if body.startswith("#") else body.find("\n#") + 1
    if at == 0 and not body.startswith("#"):
        return found
    while True:
        end = body.find("\n", at)
        match = HEADING.match(body[at:] if end < 0 else body[at:end])
        if match:
            found.append(match.group(1))
        at = body.find("\n#", at) + 1
        if at == 0:
            return found


class Hit:
    """A doc that holds every term. Its folded text is made when something
    asks for it: ranking a query of one term never does."""

    def __init__(self, key, doc):
        self.key, self.doc = key, doc
        self._text = self._squeezed = None

    @property
    def text(self):
        if self._text is None:
            self._text = self.doc.text.lower()
        return self._text

    def has(self, term):
        if term in self.text:
            return True
        if self._squeezed is None:
            self._squeezed = squeeze(self.text)
        return squeeze(term) in self._squeezed

    def weights(self, found):
        """Per term, the weight of the best place it occurs in."""
        doc = self.doc
        zones = [(KEY, self.key.lower())]
        for field, value in doc.fields.items():
            shown = frontmatter.render(value).lower()
            zones.append((TITLE if field in ("title", "session") else TAGS if field == "tags" else FIELD, shown))
        zones += [(HEAD, heading.lower()) for heading in headings(doc.body)]
        best = []
        for term in found:
            weight = BODY
            for zone, text in zones:
                if zone > weight and holds(term, text, squeeze(text)):
                    weight = zone
            best.append(weight)
        return best

    def section(self, found):
        """(the `##` section that holds the most terms, how many), or (None, 0)."""
        body, best, most = self.doc.body, None, 0
        names = sections.names(body)
        for name in names:
            if names.count(name) > 1:
                continue
            text = (name + "\n" + "\n".join(sections.lines_of(body, name))).lower()
            count = sum(1 for term in found if holds(term, text, squeeze(text)))
            if count > most:
                best, most = name, count
        return best, most

    def summary(self, found, section, size):
        """The first line of prose that holds a term, from the best section
        when there is one; else the first line of prose."""
        body = self.doc.body
        lines = sections.lines_of(body, section) if section else body.split("\n")
        first = None
        for source in (lines, body.split("\n")):
            for line in source:
                plain = line.strip()
                if not plain or plain.startswith(("#", "<!--", "```", "|--", "---")):
                    continue
                first = first or plain
                low = plain.lower()
                if any(holds(term, low, squeeze(low)) for term in found):
                    return _cut(plain, size)
        return _cut(first or "", size)


def _cut(text, size):
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def rank(hits, found, query, total, holders):
    """The hits, best first: (score, hit). `total` is how many docs were
    looked at and `holders`, per term, how many of them hold it: a term that
    few docs hold counts for more."""
    rarity = [1.0 + math.log(max(total, 1) / max(count, 1)) for count in holders]
    phrase = " ".join(query.lower().replace('"', " ").split())
    scored = []
    for hit in hits:
        score = sum(weight * rare for weight, rare in zip(hit.weights(found), rarity))
        if len(found) > 1:
            if phrase in hit.text:
                score += PHRASE
            if hit.section(found)[1] == len(found):
                score += TOGETHER
        scored.append((round(score, 3), hit))
    scored.sort(key=lambda pair: (-pair[0], pair[1].key))
    return scored
