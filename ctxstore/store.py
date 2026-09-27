"""The store: docs, their schemas, validation and the one write path."""
import fnmatch
import hashlib
import re

from . import frontmatter, fs, secrets, sections
from .contract import CtxError

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Doc:
    def __init__(self, key, data):
        self.key = key
        self.data = data
        try:
            self.text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise CtxError("SCHEMA_VIOLATION", "encoding") from None
        lines, self.body = frontmatter.split(self.text)
        self.fields = frontmatter.parse(lines)


class Store:
    def __init__(self, root, config, named):
        self.root = root
        self.config = config
        self.named = named
        self.marker = fs.marker(root)
        self.types = fs.types(root)

    # --- reading ---

    def _matches(self, key, patterns):
        return any(fnmatch.fnmatchcase(key + ".md", pattern) for pattern in patterns)

    def generated(self, key):
        return self._matches(key, self.marker["generated"])

    def keys(self):
        skip = self.marker["generated"] + self.marker["ignore"]
        return [key for key in fs.list_docs(self.root) if not self._matches(key, skip)]

    def read(self, key):
        path, key = fs.doc_path(self.root, key)
        return path, key, fs.read_bytes(path, key)

    def load(self, key):
        _, key, data = self.read(key)
        return Doc(key, data)

    def type_of(self, doc):
        name = doc.fields.get("type")
        if isinstance(name, str) and name:
            return name
        for name, schema in self.types.items():
            if self._matches(doc.key, schema.get("paths", [])):
                return name
        return None

    def of_type(self, name):
        found = []
        for key in self.keys():
            try:
                doc = self.load(key)
            except CtxError:
                continue
            if self.type_of(doc) == name:
                found.append(doc)
        return found

    # --- validation ---

    def findings(self, key, data):
        """Every violation of one doc as (code, detail); empty when it is clean."""
        try:
            doc = Doc(key, data)
        except CtxError as failure:
            return [(failure.code, failure.detail)]
        found = []
        name = self.type_of(doc)
        if name is None:
            return [("SCHEMA_VIOLATION", "type")]
        for heading in sections.duplicates(doc.body):
            found.append(("SCHEMA_VIOLATION", f"heading {heading}"))
        schema = self.types.get(name)
        if schema is None:
            return found
        for field, rule in schema.get("frontmatter", {}).items():
            problem = _field_problem(doc.fields.get(field), rule)
            if problem:
                found.append(("SCHEMA_VIOLATION", field))
        present = sections.names(doc.body)
        for heading in schema.get("sections", []):
            if heading not in present:
                found.append(("SCHEMA_VIOLATION", f"section {heading}"))
        log = schema.get("log", {})
        if log.get("grammar") and log.get("section") in present and log["section"] not in sections.duplicates(doc.body):
            grammar = re.compile(log["grammar"])
            for number, line in enumerate(sections.lines_of(doc.body, log["section"]), 1):
                if line.strip() and not line.lstrip().startswith("<!--") and not grammar.match(line):
                    found.append(("SCHEMA_VIOLATION", f"{log['section']} line {number}"))
        return found

    def audited(self):
        """(doc key -> the hashes its audit rows left it with, keys whose trail
        has a gap). A gap is a write that started from a state no row had
        left, with no `adopt` at or after it: the doc changed outside ctx in
        between. Hashes link the rows and `seq` orders them, so actors' clocks
        need not agree."""
        rows = [row for row in fs.audit_rows(self.root) if isinstance(row.get("doc"), str)]
        after, adopted = {}, {}
        for row in rows:
            after.setdefault(row["doc"], set()).add(row.get("after"))
            if row.get("verb") == "adopt":
                adopted[row["doc"]] = max(adopted.get(row["doc"], 0), _seq(row))
        broken = set()
        for row in rows:
            key = row["doc"]
            loose = row.get("before") is None or row["before"] not in after[key]
            if row.get("verb") != "adopt" and loose and _seq(row) > adopted.get(key, 0):
                broken.add(key)
        return after, broken

    # --- the write path ---

    def write(self, verb, key, change, payload="", now=None, actor=None, check=True):
        """lock → change → validate → secret scan → temp + rename → re-read and
        checksum → audit row. `change` maps the current text (None: no doc) to
        the new text. Returns the audit row."""
        if not self.named:
            raise CtxError("STORE_NOT_NAMED")
        path, key = fs.doc_path(self.root, key)
        if self.generated(key):
            raise CtxError("GENERATED", key)
        if fs.read_only(self.root):
            raise CtxError("STORE_READONLY", self.root)
        secrets.scan(payload)
        actor = actor or self.config.actor
        with fs.Lock(self.root, self.config.lock_timeout, self.config.lock_mode):
            before = fs.read_bytes(path, key) if fs.exists(path) else None
            if before is not None:
                self._owner_check(Doc(key, before), actor)
            text = change(None if before is None else before.decode("utf-8"))
            data = text.encode("utf-8")
            if check:
                found = self.findings(key, data)
                if found:
                    raise CtxError(*found[0])
            secrets.scan(text if before is None else _added(before.decode("utf-8"), text))
            written = fs.write_atomic(path, data)
            if digest(written) != digest(data):
                raise CtxError("STORE_READONLY", key)
            row = {
                "ts": now or fs.now_utc(),
                "actor": actor,
                "verb": verb,
                "doc": key,
                "before": None if before is None else digest(before),
                "after": digest(data),
            }
            row = fs.audit_append(self.root, actor, row)
        return row

    def adopt(self, key, data, now=None):
        """Record the current state of a doc that changed outside ctx."""
        if not self.named:
            raise CtxError("STORE_NOT_NAMED")
        with fs.Lock(self.root, self.config.lock_timeout, self.config.lock_mode):
            row = {
                "ts": now or fs.now_utc(),
                "actor": self.config.actor,
                "verb": "adopt",
                "doc": key,
                "before": None,
                "after": digest(data),
            }
            row = fs.audit_append(self.root, self.config.actor, row)
        return row

    def _owner_check(self, doc, actor):
        schema = self.types.get(self.type_of(doc) or "", {})
        field = schema.get("owner")
        owner = doc.fields.get(field) if field else None
        if isinstance(owner, str) and owner and owner != actor:
            raise CtxError("NOT_OWNER", doc.key)


def _seq(row):
    return row["seq"] if type(row.get("seq")) is int else 0


def _added(before, after):
    """The lines a write adds: what the secret scan of the result looks at, so
    a secret already in the doc does not block an unrelated write."""
    old = set(before.split("\n"))
    return "\n".join(line for line in after.split("\n") if line not in old)


def _field_problem(value, rule):
    if value is None or value == "" or value == []:
        return bool(rule.get("required"))
    kind = rule.get("kind", "string")
    if kind == "list":
        if not isinstance(value, list):
            return True
    elif isinstance(value, list):
        return True
    elif kind == "date" and not DATE.match(value):
        return True
    elif kind == "timestamp" and not TIMESTAMP.match(value):
        return True
    if "const" in rule and value != rule["const"]:
        return True
    if "enum" in rule and value not in rule["enum"]:
        return True
    return False
