"""`init`: a store made from a consumer's settings, schemas and templates."""
import hashlib
import json
import os
import shutil
import stat
import tempfile
import unittest

from tests.harness import FIXTURE, ctx

NOW = "2026-01-08T09:30:00Z"
TYPES = os.path.join(FIXTURE, ".ctx", "types")
TEMPLATES = os.path.join(FIXTURE, ".ctx", "templates")
SETTINGS = os.path.join(FIXTURE, "ctx-store.json")
FILES = ["ctx-store.json", ".ctx/types/epic.json", ".ctx/types/ledger.json", ".ctx/types/reference.json",
         ".ctx/types/session.json", ".ctx/templates/epic.md"]


class Init(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.store = os.path.join(os.path.realpath(self.work.name), "new", "store")
        self.types = os.path.join(self.work.name, "types")
        shutil.copytree(TYPES, self.types)
        self.env = {"CTX_STORE": self.store, "CTX_ACTOR": "tester"}

    def run_ctx(self, *args, **env):
        return ctx("--now", NOW, *args, env={**self.env, **env})

    def init(self, *args):
        return self.run_ctx("init", "--settings", SETTINGS, "--types", self.types, "--templates", TEMPLATES, *args)

    def audit(self):
        path = os.path.join(self.store, ".audit", "tester.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def write(self, path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def read(self, name):
        with open(os.path.join(self.store, name), "rb") as handle:
            return handle.read()

    def test_fresh_store(self):
        code, out, err = self.init()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [f"ok: store {self.store}, 6 written, 0 unchanged"]
                         + [f"written: {name}" for name in FILES])
        with open(SETTINGS, encoding="utf-8") as handle:
            self.assertEqual(json.loads(self.read("ctx-store.json")), json.load(handle))
        with open(os.path.join(TYPES, "epic.json"), "rb") as handle:
            self.assertEqual(self.read(".ctx/types/epic.json"), handle.read())
        rows = self.audit()
        self.assertEqual([(row["verb"], row["doc"], row["before"], row["seq"]) for row in rows],
                         [("init", name, None, number) for number, name in enumerate(FILES, 1)])
        self.assertEqual(rows[0]["after"], hashlib.sha256(self.read("ctx-store.json")).hexdigest())
        self.assertEqual(rows[0]["ts"], NOW)
        self.assertEqual(self.run_ctx("validate"), (0, "ok: 0 docs checked\n", ""))
        self.assertEqual(self.run_ctx("validate", "--changed"), (0, "ok: 0 docs checked\n", ""))
        self.assertEqual(self.run_ctx("doctor")[0], 0)
        self.assertEqual(self.run_ctx("new", "epic", "epics/first", "--title", "First")[0], 0)

    def test_second_run_changes_nothing(self):
        self.init()
        code, out, err = self.init("--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["data"], {"store": self.store, "written": [], "unchanged": FILES})
        self.assertEqual(len(self.audit()), 6)

    def test_marker_only(self):
        self.assertEqual(self.run_ctx("init")[1].splitlines(),
                         [f"ok: store {self.store}, 1 written, 0 unchanged", "written: ctx-store.json"])
        self.assertEqual(json.loads(self.read("ctx-store.json")), {"schema_version": 1})
        # without --settings an existing marker is kept
        self.write(os.path.join(self.store, "ctx-store.json"), '{"schema_version": 1, "ignore": ["x.md"]}')
        self.assertEqual(self.run_ctx("init", "--types", self.types)[0], 0)
        self.assertEqual(json.loads(self.read("ctx-store.json")), {"schema_version": 1, "ignore": ["x.md"]})

    def test_a_differing_file_is_refused(self):
        self.init()
        self.write(os.path.join(self.types, "epic.json"), '{"sections": ["Goal"]}\n')
        self.write(os.path.join(self.types, "extra.json"), "{}\n")
        before = self.read(".ctx/types/epic.json")
        code, out, err = self.init()
        self.assertEqual((code, out, err), (3, "", "SCHEMA_VIOLATION .ctx/types/epic.json: schema violation\n"))
        self.assertEqual(self.read(".ctx/types/epic.json"), before)
        self.assertFalse(os.path.exists(os.path.join(self.store, ".ctx", "types", "extra.json")))
        self.assertEqual(len(self.audit()), 6)

    def test_replace(self):
        self.init()
        self.write(os.path.join(self.types, "epic.json"), '{"sections": ["Goal"]}\n')
        before = hashlib.sha256(self.read(".ctx/types/epic.json")).hexdigest()
        os.makedirs(os.path.join(self.store, ".ctx", "types"), exist_ok=True)
        self.write(os.path.join(self.store, ".ctx", "types", "local.json"), "{}\n")
        code, out, _ = self.init("--replace")
        self.assertEqual(code, 0)
        self.assertIn("ok: store", out)
        self.assertIn("written: .ctx/types/epic.json\n", out)
        self.assertEqual(self.read(".ctx/types/epic.json"), b'{"sections": ["Goal"]}\n')
        self.assertTrue(os.path.exists(os.path.join(self.store, ".ctx", "types", "local.json")))
        row = self.audit()[-1]
        self.assertEqual((len(self.audit()), row["doc"], row["before"]), (7, ".ctx/types/epic.json", before))

    def test_upgrade_replaces_only_what_init_wrote(self):
        """--upgrade (#57): a file still as init left it is replaced, one edited
        since is kept and reported."""
        self.init()
        self.write(os.path.join(self.store, ".ctx", "types", "ledger.json"), '{"sections": ["Mine"]}\n')
        self.write(os.path.join(self.types, "epic.json"), '{"sections": ["Goal"]}\n')
        self.write(os.path.join(self.types, "ledger.json"), '{"sections": ["Theirs"]}\n')
        code, out, _ = self.init("--upgrade")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(f"ok: store {self.store}, 1 written, 4 unchanged, 1 kept\n"), out)
        self.assertIn("written: .ctx/types/epic.json\n", out)
        self.assertIn("kept: .ctx/types/ledger.json\n", out)
        self.assertEqual(self.read(".ctx/types/epic.json"), b'{"sections": ["Goal"]}\n')
        self.assertEqual(self.read(".ctx/types/ledger.json"), b'{"sections": ["Mine"]}\n')
        self.assertEqual(len(self.audit()), 7)
        data = json.loads(self.init("--upgrade", "--json")[1])["data"]
        self.assertEqual((data["written"], data["kept"]), ([], [".ctx/types/ledger.json"]))

    def test_upgrade_records_a_baseline_for_a_store_made_by_hand(self):
        self.init()
        os.unlink(os.path.join(self.store, ".audit", "tester.jsonl"))  # the same files, no init rows: by hand
        self.write(os.path.join(self.types, "epic.json"), '{"sections": ["Goal"]}\n')
        code, out, _ = self.init("--upgrade")  # nothing proves epic.json unedited: kept
        self.assertEqual(code, 0)
        self.assertIn("kept: .ctx/types/epic.json\n", out)
        self.assertEqual({row["doc"] for row in self.audit()}, set(FILES) - {".ctx/types/epic.json"})
        self.assertTrue(all(row["before"] == row["after"] for row in self.audit()))
        self.assertEqual(self.run_ctx("validate", "--changed")[0], 0)
        shutil.copy(os.path.join(TYPES, "epic.json"), os.path.join(self.types, "epic.json"))
        self.assertIn("unchanged: .ctx/types/epic.json\n", self.init("--upgrade")[1])  # its baseline now
        self.write(os.path.join(self.types, "epic.json"), '{"sections": ["Goal"]}\n')
        self.assertIn("written: .ctx/types/epic.json\n", self.init("--upgrade")[1])

    def test_upgrade_and_replace_are_one_or_the_other(self):
        self.assertEqual(self.init("--upgrade", "--replace")[:3:2], (1, "USAGE --upgrade: bad command line\n"))

    def test_bad_settings_create_nothing(self):
        settings = os.path.join(self.work.name, "settings.json")
        cases = (('{"schema_version": 1, "colour": true}', "colour"), ('{"schema_version": 2}', "schema_version"),
                 ('{"ignore": "README.md"}', settings), ("[]", settings), ("{", settings))
        for text, detail in cases:
            self.write(settings, text)
            code, out, err = self.run_ctx("init", "--settings", settings, "--types", self.types)
            self.assertEqual((code, err), (3, f"SCHEMA_VIOLATION {detail}: schema violation\n"), text)
            self.assertFalse(os.path.exists(os.path.dirname(self.store)))

    def test_bad_schema_writes_nothing(self):
        self.write(os.path.join(self.types, "zeta.json"), '{"sections": "Goal"}')
        code, _, err = self.init()
        where = os.path.join(self.types, "zeta.json")
        self.assertEqual((code, err), (3, f"SCHEMA_VIOLATION {where}: schema violation\n"))
        self.assertFalse(os.path.exists(os.path.dirname(self.store)))
        self.write(where, "not json")
        self.assertEqual(self.init()[0], 3)

    def test_other_files_are_usage(self):
        for name in ("notes.txt", "Epic.json", ".hidden.json"):
            path = os.path.join(self.types, name)
            self.write(path, "{}")
            self.assertEqual(self.init()[2], f"USAGE {path}: bad command line\n")
            os.unlink(path)
        os.mkdir(os.path.join(self.types, "nested"))
        self.assertEqual(self.init()[0], 1)
        self.assertEqual(self.run_ctx("init", "--types", os.path.join(self.work.name, "none"))[2],
                         "USAGE --types: bad command line\n")
        self.assertFalse(os.path.exists(os.path.dirname(self.store)))

    def test_store_must_be_one_markdown_path(self):
        self.assertEqual(self.run_ctx("init", CTX_STORE="memory://x"), (1, "", "USAGE memory://x: bad command line\n"))
        self.assertEqual(self.run_ctx("init", CTX_STORE=self.store + os.pathsep + self.store)[0], 1)
        code, _, err = ctx("init", env={"CTX_ACTOR": "tester"})
        self.assertEqual((code, err), (5, "STORE_NOT_NAMED: a write needs CTX_STORE or --store\n"))
        self.assertEqual(self.run_ctx("--store", "markdown://" + self.store, "init")[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(self.store, "ctx-store.json")))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_unwritable_parent(self):
        parent = os.path.dirname(self.store)
        os.makedirs(parent)
        os.chmod(parent, stat.S_IRUSR | stat.S_IXUSR)
        try:
            code, _, err = self.init()
        finally:
            os.chmod(parent, stat.S_IRWXU)
        self.assertEqual((code, err), (5, f"STORE_READONLY {self.store}: store is read-only or unwritable\n"))
        self.assertFalse(os.path.exists(self.store))

    def test_not_over_mcp(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ctx_init", "arguments": {}}}
        code, out, _ = ctx("mcp", env=self.env, stdin=json.dumps(message) + "\n")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (0, -32602))
        self.assertFalse(os.path.exists(self.store))


if __name__ == "__main__":
    unittest.main()
