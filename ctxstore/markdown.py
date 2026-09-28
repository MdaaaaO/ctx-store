"""The Markdown backend: a directory of `*.md` files, the default store.

Files stay hand-editable, so this is the backend where a doc can change
behind ctx's back, and the one with a walk-up and lock modes. Everything it
does on disk goes through `fs.py`.
"""
import os.path

from . import fs
from .backend import SCHEME, Backend, check_settings
from .contract import CtxError


class MarkdownBackend(Backend):
    name = "markdown"
    outside_writes = True

    def __init__(self, locator, config):
        match = SCHEME.match(locator)
        path = match.group(2) if match else locator
        if not fs.is_store(path):
            raise CtxError("NO_STORE", locator)
        super().__init__(fs.canonical(path), config)
        self.root = self.locator

    @staticmethod
    def find(start):
        return fs.find_store(start)

    def describe(self):
        return {
            "path": self.root,
            "schema_version": fs.schema_version(self.root),
            "filesystem": fs.filesystem(self.root),
            "read_only": self.read_only(),
            "lock_mode": self.config.lock_mode or fs.lock_mode(self.root),
        }

    def settings(self):
        return check_settings(fs.marker(self.root), fs.MARKER)

    def types(self):
        return fs.types(self.root)

    def template(self, name):
        return fs.template(self.root, name)

    def key(self, key):
        return fs.doc_path(self.root, key)[1]

    def _path(self, key):
        return os.path.join(self.root, *key.split("/")) + ".md"

    def folder(self, key):
        return fs.folder(self.root, key)

    def keys(self):
        return fs.list_docs(self.root)

    def exists(self, key):
        return fs.exists(self._path(key))

    def read(self, key):
        return fs.read_bytes(self._path(key), key)

    def read_head(self, key, size):
        return fs.read_bytes(self._path(key), key, size)

    def write(self, key, data):
        return fs.write_atomic(self._path(key), data)

    def remove(self, key):
        fs.remove(self._path(key))

    def read_only(self):
        return fs.read_only(self.root)

    def lock(self):
        return fs.Lock(self.root, self.config.lock_timeout, self.config.lock_mode)

    def audit_append(self, actor, row):
        return fs.audit_append(self.root, actor, row)

    def audit_rows(self):
        return fs.audit_rows(self.root)

    def audit_archive(self, actor):
        return fs.audit_archive(self.root, actor)

    def commit(self, actor, message, debounce, now):
        return fs.git_commit(self.root, actor, message, debounce, now)
