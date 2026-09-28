"""Standard library only, no network, one module on the filesystem."""
import ast
import os
import sys
import unittest

from tests.harness import ROOT

PACKAGE = os.path.join(ROOT, "ctxstore")
NETWORK = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "asyncio", "xmlrpc"}
FILESYSTEM = {"pathlib", "shutil", "tempfile", "fcntl", "glob"}


def _modules():
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
                yield name, ast.parse(handle.read(), name)


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module.split(".")[0]


class Hygiene(unittest.TestCase):
    def test_stdlib_only_and_no_network(self):
        for name, tree in _modules():
            for module in _imports(tree):
                self.assertIn(module, sys.stdlib_module_names, f"{name}: {module}")
                self.assertNotIn(module, NETWORK, f"{name}: {module}")

    def test_one_module_touches_the_filesystem(self):
        for name, tree in _modules():
            if name == "fs.py":
                continue
            self.assertFalse(FILESYSTEM & set(_imports(tree)), name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "open", name)


    def test_the_connector_is_standard_library_and_outside_the_core(self):
        folder = os.path.join(ROOT, "ctxserve")
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py"):
                with open(os.path.join(folder, name), encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), name)
                for module in _imports(tree):
                    self.assertTrue(module in sys.stdlib_module_names or module == "ctxstore", f"{name}: {module}")
        for name, tree in _modules():
            self.assertNotIn("ctxserve", list(_imports(tree)), name)

    def test_the_core_reaches_storage_through_the_backend_only(self):
        """Store data is the backend's; `fs` is for the Markdown backend and
        for files of a run that are not store data; `init` (bootstrap.py)
        makes a Markdown store before its backend can open it."""
        allowed = {"fs.py", "markdown.py", "cli.py", "spec.py", "reads.py", "doctor.py", "bootstrap.py", "__init__.py"}
        for name, tree in _modules():
            if name in allowed:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                    self.assertNotIn("fs", [alias.name for alias in node.names], name)
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "fs":
                    self.fail(name)


if __name__ == "__main__":
    unittest.main()
