"""The store: docs, their schemas, validation and the one write path."""
import fnmatch
import hashlib
import re

from . import clock, frontmatter, secrets, sections
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
    def __init__(self, backend, config, named):
        self.backend = backend
        self.locator = backend.locator
        self.config = config
        self.named = named
        self.marker = backend.settings()
        self.types = backend.types()
        for name, schema in self.types.items():
            _check_schema(name, schema)

    # --- reading ---

    def _matches(self, key, patterns):
        return any(fnmatch.fnmatchcase(key + ".md", pattern) for pattern in patterns)

    def generated(self, key):
        return self._matches(key, self.marker["generated"])

    def keys(self):
        skip = self.marker["generated"] + self.marker["ignore"]
        return [key for key in self.backend.keys() if not self._matches(key, skip)]

    def key(self, key):
        """The canonical key of a doc. An ignored key is not a doc."""
        key = self.backend.key(key)
        if self._matches(key, self.marker["ignore"]):
            raise CtxError("NO_SUCH_DOC", key)
        return key

    def read(self, key):
        return self.backend.read(self.key(key))

    def has(self, key):
        try:
            return self.backend.exists(self.key(key))
        except CtxError:
            return False

    def target(self, key):
        """The canonical key of a doc a write is about to change."""
        if not self.named:
            raise CtxError("STORE_NOT_NAMED")
        key = self.key(key)
        if self.generated(key):
            raise CtxError("GENERATED", key)
        return key

    def load(self, key):
        key = self.key(key)
        return Doc(key, self.backend.read(key))

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
            for number, line in sections.entries(sections.lines_of(doc.body, log["section"])):
                if not grammar.match(line):
                    found.append(("SCHEMA_VIOLATION", f"{log['section']} line {number}"))
        return found

    def audited(self):
        """(doc key -> the hashes its audit rows left it with, keys whose trail
        has a gap). A gap is a write that started from a state no row had
        left, with no `adopt` at or after it: the doc changed outside ctx in
        between. Hashes link the rows and `seq` orders them, so actors' clocks
        need not agree."""
        rows = [row for row in self.backend.audit_rows() if isinstance(row.get("doc"), str)]
        after, adopted = {}, {}
        for row in rows:
            after.setdefault(row["doc"], set()).add(row.get("after"))
            if row.get("verb") == "adopt":
                adopted[row["doc"]] = max(adopted.get(row["doc"], 0), _seq(row))
        broken = set()
        last = {}
        for row in sorted(rows, key=_seq):
            last[row["doc"]] = row.get("after")
        self.removed = {key for key, state in last.items() if state is None}
        for row in rows:
            key = row["doc"]
            loose = row.get("before") is not None and row["before"] not in after[key]
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
        key = self.key(key)
        if self.generated(key):
            raise CtxError("GENERATED", key)
        secrets.scan(payload)
        with self.locked():
            return self.apply(verb, key, change, now, actor, check)

    def locked(self):
        """The store lock, for a write that touches more than one doc."""
        if not self.named:
            raise CtxError("STORE_NOT_NAMED")
        if self.backend.read_only():
            raise CtxError("STORE_READONLY", self.locator)
        return self.backend.lock()

    def apply(self, verb, key, change, now=None, actor=None, check=True):
        """One doc's write, under a lock the caller holds. `change` returning
        None removes the doc."""
        key = self.key(key)
        if self.generated(key):
            raise CtxError("GENERATED", key)
        actor = actor or self.config.actor
        before = self.backend.read(key) if self.backend.exists(key) else None
        if before is not None:
            self._owner_check(key, before, actor)
        text = change(None if before is None else _decode(before))
        if text is None:
            if before is None:
                raise CtxError("NO_SUCH_DOC", key)
            self.backend.remove(key)
            data = None
        else:
            data = text.encode("utf-8")
            if check:
                found = self.findings(key, data)
                if found:
                    raise CtxError(*found[0])
            secrets.scan(text if before is None else _added(_decode(before), text))
            written = self.backend.write(key, data)
            if digest(written) != digest(data):
                raise CtxError("STORE_READONLY", key)
        row = {
            "ts": now or clock.now_utc(),
            "actor": actor,
            "verb": verb,
            "doc": key,
            "before": None if before is None else digest(before),
            "after": None if data is None else digest(data),
        }
        return self.backend.audit_append(actor, row)

    def adopt(self, key, data, now=None):
        """Record the current state of a doc that changed outside ctx."""
        if not self.named:
            raise CtxError("STORE_NOT_NAMED")
        with self.locked():
            row = {
                "ts": now or clock.now_utc(),
                "actor": self.config.actor,
                "verb": "adopt",
                "doc": key,
                "before": None,
                "after": digest(data),
            }
            row = self.backend.audit_append(self.config.actor, row)
        return row

    def _owner_check(self, key, data, actor):
        try:
            doc = Doc(key, data)
        except CtxError:
            return  # a doc that does not parse names no owner
        schema = self.types.get(self.type_of(doc) or "", {})
        field = schema.get("owner")
        owner = doc.fields.get(field) if field else None
        if isinstance(owner, str) and owner and owner != actor:
            raise CtxError("NOT_OWNER", doc.key)


def _decode(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise CtxError("SCHEMA_VIOLATION", "encoding") from None


def _check_schema(name, schema):
    """A type schema that cannot be applied is a violation named by its file,
    found when the store is opened, not in the middle of a write."""
    where = f".ctx/types/{name}.json"
    shapes = {"paths": list, "frontmatter": dict, "sections": list, "log": dict, "owner": str}
    if not isinstance(schema, dict):
        raise CtxError("SCHEMA_VIOLATION", where)
    for key, shape in shapes.items():
        if key in schema and not isinstance(schema[key], shape):
            raise CtxError("SCHEMA_VIOLATION", where)
    if not all(isinstance(rule, dict) for rule in schema.get("frontmatter", {}).values()):
        raise CtxError("SCHEMA_VIOLATION", where)
    if not all(isinstance(item, str) for item in schema.get("paths", []) + schema.get("sections", [])):
        raise CtxError("SCHEMA_VIOLATION", where)
    log = schema.get("log", {})
    if log.get("order", "oldest-first") not in ("oldest-first", "newest-first"):
        raise CtxError("SCHEMA_VIOLATION", where)
    if "section" in log and not isinstance(log["section"], str):
        raise CtxError("SCHEMA_VIOLATION", where)
    try:
        re.compile(log.get("grammar", ""))
    except (re.error, TypeError):
        raise CtxError("SCHEMA_VIOLATION", where) from None


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
