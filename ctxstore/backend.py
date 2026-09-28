"""The backend boundary: what a store's storage has to provide.

The interface (verbs, errors, the doc model) is the contract callers depend
on. A backend keeps the docs; the core never asks where. Markdown files are
the default backend (`markdown.py`); `memory://<name>` keeps docs in the
process and is the reference for writing another one.

A store is named by a locator: a directory path, or `<scheme>://<rest>`.
"""
import contextlib
import json
import os
import re
import threading

from .contract import CtxError

SCHEME = re.compile(r"^([a-z][a-z0-9+.-]+)://(.*)$", re.S)
SETTINGS = ("generated", "ignore")


class Backend:
    """Every method a backend provides. Doc keys are `/`-separated, without
    an extension; doc content is bytes (UTF-8 text with frontmatter)."""

    name = ""
    #: whether docs can change without ctx (files can; rows behind an API cannot)
    outside_writes = False

    def __init__(self, locator, config):
        self.locator = locator
        self.config = config

    def describe(self):
        """What `doctor` reports for the store, beside backend and locator."""
        raise NotImplementedError

    def settings(self):
        """The store's settings: `schema_version`, `generated`, `ignore`, `resolve`."""
        raise NotImplementedError

    def types(self):
        """Type name -> schema."""
        raise NotImplementedError

    def template(self, name):
        """The scaffold of a doc type, or None."""
        raise NotImplementedError

    def key(self, key):
        """The canonical key, or PATH_ESCAPE for one that is not the store's."""
        raise NotImplementedError

    def folder(self, key):
        """The canonical key of a group of docs ('' for all), or None."""
        raise NotImplementedError

    def keys(self):
        """Every doc key, sorted."""
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError

    def read(self, key):
        """The doc's bytes, or NO_SUCH_DOC."""
        raise NotImplementedError

    def write(self, key, data):
        """Store the doc whole or not at all; returns what a read now gives."""
        raise NotImplementedError

    def remove(self, key):
        raise NotImplementedError

    def read_only(self):
        raise NotImplementedError

    def lock(self):
        """Context manager: one writer at a time, LOCK_TIMEOUT after
        `config.lock_timeout` seconds."""
        raise NotImplementedError

    def audit_append(self, actor, row):
        """Add one audit row, numbered `seq`; returns it. Called under the lock."""
        raise NotImplementedError

    def audit_rows(self):
        raise NotImplementedError


def check_settings(data, where):
    """Settings as the core uses them, from what a backend has stored."""
    if not isinstance(data, dict):
        raise CtxError("SCHEMA_VIOLATION", where)
    version = data.get("schema_version")
    if type(version) is not int or version < 1:
        raise CtxError("SCHEMA_VIOLATION", where)
    settings = {"schema_version": version}
    for key in SETTINGS:
        value = data.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise CtxError("SCHEMA_VIOLATION", where)
        settings[key] = value
    settings["resolve"] = data.get("resolve", {})
    if not isinstance(settings["resolve"], dict):
        raise CtxError("SCHEMA_VIOLATION", where)
    return settings


def lexical_key(key):
    """Canonical key for a backend without a filesystem under it."""
    if not isinstance(key, str) or not key or "\0" in key or key.startswith("/"):
        raise CtxError("PATH_ESCAPE", str(key))
    parts = (key[:-3] if key.endswith(".md") else key).split("/")
    if any(not part or part.startswith(".") for part in parts):
        raise CtxError("PATH_ESCAPE", key)
    return "/".join(parts)


class MemoryBackend(Backend):
    """Docs in the process: `memory://<name>`. For tests of callers, and the
    shortest complete backend to read."""

    name = "memory"
    spaces = {}

    @classmethod
    def space(cls, name, docs=None, settings=None, types=None, templates=None, read_only=False):
        """Create (or replace) a named space and return it."""
        cls.spaces[name] = {
            "docs": {key: bytes(data) for key, data in (docs or {}).items()},
            "settings": settings or {"schema_version": 1},
            "types": types or {},
            "templates": templates or {},
            "audit": [],
            "read_only": read_only,
            "lock": threading.Lock(),
        }
        return cls.spaces[name]

    def __init__(self, locator, config):
        super().__init__(locator, config)
        rest = SCHEME.match(locator).group(2)
        if rest not in self.spaces:
            raise CtxError("NO_STORE", locator)
        self.data = self.spaces[rest]

    def describe(self):
        return {"schema_version": self.settings()["schema_version"], "read_only": self.read_only()}

    def settings(self):
        return check_settings(self.data["settings"], "settings")

    def types(self):
        return json.loads(json.dumps(self.data["types"]))

    def template(self, name):
        return self.data["templates"].get(name)

    def key(self, key):
        return lexical_key(key)

    def folder(self, key):
        if key in ("", ".", "/"):
            return ""
        try:
            key = lexical_key(key)
        except CtxError:
            return None
        return key if any(k.startswith(key + "/") for k in self.data["docs"]) else None

    def keys(self):
        return sorted(self.data["docs"])

    def exists(self, key):
        return key in self.data["docs"]

    def read(self, key):
        if key not in self.data["docs"]:
            raise CtxError("NO_SUCH_DOC", key)
        return self.data["docs"][key]

    def write(self, key, data):
        self.data["docs"][key] = bytes(data)
        return self.data["docs"][key]

    def remove(self, key):
        del self.data["docs"][key]

    def read_only(self):
        return self.data["read_only"]

    @contextlib.contextmanager
    def lock(self):
        if not self.data["lock"].acquire(timeout=self.config.lock_timeout):
            raise CtxError("LOCK_TIMEOUT", "store")
        try:
            yield self
        finally:
            self.data["lock"].release()

    def audit_append(self, actor, row):
        row = {**row, "seq": len(self.data["audit"]) + 1}
        self.data["audit"].append(row)
        return row

    def audit_rows(self):
        return list(self.data["audit"])


def split_locators(value):
    """The locators of a `CTX_STORE` list. The list is separated like PATH;
    the `:` of `<scheme>://` does not separate."""
    parts, found = [part for part in value.split(os.pathsep)], []
    index = 0
    while index < len(parts):
        part = parts[index]
        following = parts[index + 1] if index + 1 < len(parts) else ""
        if os.pathsep == ":" and re.fullmatch(r"[a-z][a-z0-9+.-]+", part) and following.startswith("//"):
            part, index = part + ":" + following, index + 1
        if part:
            found.append(part)
        index += 1
    return found


def open_store(locator, config):
    """The backend of one locator. A path is a Markdown store."""
    from .markdown import MarkdownBackend
    backends = {"markdown": MarkdownBackend, "memory": MemoryBackend}
    match = SCHEME.match(locator)
    if not match:
        return MarkdownBackend(locator, config)
    if match.group(1) not in backends:
        raise CtxError("NO_STORE", locator)
    return backends[match.group(1)](locator, config)


def open_stores(config, start):
    """The backends of this run: the configured list, else the Markdown store
    found by walking up from `start`."""
    from .markdown import MarkdownBackend
    if config.stores:
        return [open_store(locator, config) for locator in config.stores]
    found = MarkdownBackend.find(start) if config.walk else None
    if found is None:
        raise CtxError("NO_STORE")
    return [MarkdownBackend(found, config)]
