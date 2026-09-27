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


if __name__ == "__main__":
    unittest.main()
