"""A doc may be named by the path the caller sees: absolute, or from its working directory."""
import json
import os
import unittest

from tests.harness import ctx
from tests.test_store import EPIC, NOW, StoreCase


class DocPaths(StoreCase):
    def setUp(self):
        super().setUp()
        self.parent = os.path.realpath(self.work.name)
        self.store = os.path.join(self.parent, "store")  # the real path, as ctx sees it
        self.env["CTX_STORE"] = self.store
        self.name = os.path.basename(self.store)

    def run_in(self, cwd, *args):
        return ctx("--now", NOW, "--json", *args, env=self.env, cwd=cwd)

    def doc_of(self, result):
        self.assertEqual(result[0], 0, result)
        return json.loads(result[1])["data"]["doc"]

    def test_absolute_inside(self):
        absolute = os.path.join(self.store, *EPIC.split("/")) + ".md"
        self.assertEqual(self.doc_of(self.run_in(self.parent, "get", absolute)), EPIC)
        self.assertEqual(self.doc_of(self.run_in(self.parent, "get", absolute[:-3])), EPIC)

    def test_cwd_relative_inside(self):
        key = f"{self.name}/{EPIC}.md"
        self.assertEqual(self.doc_of(self.run_in(self.parent, "get", key)), EPIC)

    def test_dot_named_store_from_its_parent(self):
        hidden = os.path.join(self.parent, ".context")
        os.rename(self.store, hidden)
        self.env["CTX_STORE"] = hidden
        self.assertEqual(self.doc_of(self.run_in(self.parent, "get", f".context/{EPIC}.md")), EPIC)

    def test_cwd_inside_the_store(self):
        folder = os.path.join(self.store, "epics")
        self.assertEqual(self.doc_of(self.run_in(folder, "get", "sample-rollout.md")), EPIC)

    def test_canonical_key_in_log_and_audit(self):
        absolute = os.path.join(self.store, *EPIC.split("/")) + ".md"
        self.assertEqual(self.doc_of(self.run_in(self.parent, "log", absolute, "via path", "--section", "Session log")), EPIC)
        self.assertEqual([row["doc"] for row in self.audit()], [EPIC])
        self.assertIn("via path", self.text(EPIC))

    def test_outside_is_path_escape(self):
        outside = os.path.join(self.parent, "outside.md")
        with open(outside, "w") as handle:
            handle.write("---\ntitle: O\ntype: reference\n---\n")
        for key in (outside, "../outside", self.store):
            result = self.run_in(self.parent, "get", key)
            self.assertEqual(result[0], 3, key)
            self.assertEqual(json.loads(result[1])["error"]["code"], "PATH_ESCAPE", key)
            self.assertEqual(json.loads(result[1])["error"]["detail"], key)

    def test_dot_directory_is_path_escape(self):
        key = os.path.join(self.store, ".ctx", "types", "epic.json")
        self.assertEqual(self.run_in(self.parent, "get", key)[0], 3)
        self.assertEqual(self.run_in(self.parent, "get", f"{self.name}/.audit/x")[0], 3)

    def test_store_relative_reading_wins(self):
        # `<store>/<name>/x.md` and `<store>/x.md` both exist; from the parent directory the key
        # `<name>/x.md` could be either, and the store-relative one is the doc.
        self.put(f"{self.name}/x", "---\ntitle: Nested\ntype: reference\n---\n\n# Nested\n")
        self.put("x", "---\ntitle: Top\ntype: reference\n---\n\n# Top\n")
        result = self.run_in(self.parent, "get", f"{self.name}/x.md")
        self.assertEqual(self.doc_of(result), f"{self.name}/x")
        self.assertIn("# Nested", json.loads(result[1])["data"]["text"])

    def test_new_doc_is_never_redirected(self):
        # Neither reading names a file yet: a new doc goes where the store-relative key says,
        # from the parent directory and from a folder inside the store alike.
        self.assertEqual(self.doc_of(self.run_in(self.parent, "new", "reference", "reference/fresh")), "reference/fresh")
        self.assertTrue(os.path.isfile(os.path.join(self.store, "reference", "fresh.md")))
        folder = os.path.join(self.store, "epics")
        self.assertEqual(self.doc_of(self.run_in(folder, "new", "reference", "reference/inside")), "reference/inside")
        self.assertTrue(os.path.isfile(os.path.join(self.store, "reference", "inside.md")))
        self.assertFalse(os.path.exists(os.path.join(folder, "reference")))


if __name__ == "__main__":
    unittest.main()
