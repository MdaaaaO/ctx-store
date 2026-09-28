"""The backend boundary: the same calls give the same answers whichever
backend keeps the docs."""
import io
import json
import os
import shutil
import tempfile
import unittest

from ctxstore import backend, cli
from ctxstore.backend import MemoryBackend
from tests.harness import FIXTURE, ctx

NOW = "2026-01-08T09:30:00Z"
EPIC = "epics/sample-rollout"

# A session of calls that covers every built verb, failures included.
SCRIPT = (
    ("validate",),
    ("validate", "--changed", "--adopt"),
    ("brief", "--registry"),
    ("brief", EPIC),
    ("brief", "--session", "sid-alpha"),
    ("log", EPIC, "region three done."),
    ("log", EPIC, "--section", "Goal ", "x"),
    ("log", "ledger", "| 2026-01-08 | appended |"),
    ("fm", EPIC, "status", "paused"),
    ("fm", EPIC, "status", "asleep"),
    ("fm", "sessions/alpha-rollout", "status", "ended"),
    ("touch", "--session", "sid-beta", "--working", "round two"),
    ("new", "epic", "epics/next", "--title", "Next thing"),
    ("new", "epic", "epics/next"),
    ("create", "notes/links", "--", "---\ntitle: Links\ntype: reference\n---\n\nSee [locks](../reference/lock-modes.md) and [[lock-modes]].\n"),
    ("str_replace", "notes/links", "--old", "See", "--new", "Read"),
    ("str_replace", "notes/links", "--old", "absent"),
    ("insert", "notes/links", "--line", "7", "One more line."),
    ("rename", "reference/lock-modes", "archive/locks"),
    ("view", "notes/links"),
    ("view",),
    ("view", "sessions"),
    ("get", EPIC, "--section", "Session log", "--tail", "2"),
    ("get", "archive/locks"),
    ("find", "region"),
    ("find", "--type", "session", "--budget", "120"),
    ("resolve", "EX-2"),
    ("resolve", "EX-99"),
    ("log", EPIC, "--", "ghp_" + "a" * 36),
    ("log", "../outside", "--section", "Log", "x"),
    ("log", "README", "--section", "Log", "x"),
    ("log", "INDEX", "--section", "Log", "x"),
    ("delete", "notes/links"),
    ("delete", "notes/links"),
    ("validate", "--changed"),
    ("validate",),
    ("migrate", "--check"),
    ("migrate", "--dry-run"),
    ("maintain",),
    ("--now", "2026-03-01T00:00:00Z", "maintain"),
    ("maintain",),
    ("validate", "--changed"),
    ("--json", "brief", EPIC),
    ("--json", "log", "epics/none", "x"),
)


def run(args, locator, **env):
    """One call in this process: (exit, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    environ = {"CTX_STORE": locator, "CTX_ACTOR": "tester", "CTX_NO_WALK": "1", **env}
    code = cli.main(["--now", NOW, *args], environ, out, err)
    return code, out.getvalue(), err.getvalue()


def fixture_space(name, **more):
    """The fixture store, loaded into a memory space."""
    docs, types, templates = {}, {}, {}
    for folder, _, files in os.walk(FIXTURE):
        for file in files:
            path = os.path.join(folder, file)
            inside = os.path.relpath(path, FIXTURE).replace(os.sep, "/")
            with open(path, "rb") as handle:
                data = handle.read()
            if inside.startswith(".ctx/types/"):
                types[file[:-5]] = json.loads(data)
            elif inside.startswith(".ctx/templates/"):
                templates[file[:-3]] = data.decode("utf-8")
            elif inside == "ctx-store.json":
                settings = json.loads(data)
            elif inside.endswith(".md"):
                docs[inside[:-3]] = data
    return MemoryBackend.space(name, docs, settings, types, templates, **more)


class Parity(unittest.TestCase):
    def test_every_verb_answers_the_same_on_both_backends(self):
        with tempfile.TemporaryDirectory() as work:
            store = os.path.join(work, "store")
            shutil.copytree(FIXTURE, store)
            space = fixture_space("parity")
            for number, args in enumerate(SCRIPT):
                files = run(args, store)
                memory = run(args, "memory://parity")
                self.assertEqual(files, memory, f"call {number}: {args}")
            on_disk = {}
            for folder, folders, names in os.walk(store):
                folders[:] = [name for name in folders if not name.startswith(".")]
                for name in names:
                    if not name.endswith(".md"):
                        continue
                    path = os.path.join(folder, name)
                    with open(path, "rb") as handle:
                        on_disk[os.path.relpath(path, store)[:-3].replace(os.sep, "/")] = handle.read()
            self.assertEqual(on_disk, space["docs"])
            rows = [row for _, row in sorted(
                (row["seq"], row) for row in backend.open_store(store, _Config()).audit_rows())]
            self.assertEqual(rows, space["audit"])
            self.assertGreater(len(rows), 15)


class _Config:
    lock_timeout = 1.0
    lock_mode = None


class Memory(unittest.TestCase):
    def test_read_only(self):
        fixture_space("frozen", read_only=True)
        self.assertEqual(run(("log", EPIC, "x"), "memory://frozen"),
                         (5, "", "STORE_READONLY memory://frozen: store is read-only or unwritable\n"))
        self.assertEqual(run(("log", EPIC, "--", "ghp_" + "a" * 36), "memory://frozen"),
                         (5, "", "STORE_READONLY memory://frozen: store is read-only or unwritable\n"))
        self.assertEqual(run(("brief", "--registry"), "memory://frozen")[0], 0)

    def test_lock_timeout(self):
        space = fixture_space("held")
        space["lock"].acquire()
        try:
            self.assertEqual(run(("log", EPIC, "x"), "memory://held", CTX_LOCK_TIMEOUT="0.1"),
                             (4, "", "LOCK_TIMEOUT store: lock timeout\n"))
        finally:
            space["lock"].release()

    def test_keys(self):
        fixture_space("keys")
        for key in ("../x", "/abs", "a//b", ".hidden/x", "a/.b", ""):
            code, _, err = run(("get", "--", key), "memory://keys")
            self.assertIn(code, (1, 3), key)
        self.assertEqual(run(("get", "epics/sample-rollout.md", "--section", "Goal"), "memory://keys")[0], 0)

    def test_doctor(self):
        fixture_space("seen")
        code, out, _ = run(("doctor", "--json"), "memory://seen")
        self.assertEqual(json.loads(out)["data"]["stores"],
                         [{"backend": "memory", "locator": "memory://seen", "read_only": False, "schema_version": 1}])
        self.assertIn("store: memory://seen (memory, from env)\n  schema version: 1\n  read-only: no\n",
                      run(("doctor",), "memory://seen")[1])

    def test_a_broken_space_is_one_error_line(self):
        MemoryBackend.space("broken", settings={"schema_version": "one"})
        self.assertEqual(run(("validate",), "memory://broken"), (3, "", "SCHEMA_VIOLATION settings: schema violation\n"))


class Locators(unittest.TestCase):
    def test_a_path_is_a_markdown_store(self):
        code, out, _ = ctx("doctor", "--json", env={"CTX_STORE": "markdown://" + FIXTURE})
        store = json.loads(out)["data"]["stores"][0]
        self.assertEqual((store["backend"], store["locator"]), ("markdown", os.path.realpath(FIXTURE)))
        self.assertEqual(ctx("brief", "--registry", "--store", "markdown://" + FIXTURE)[0], 0)

    def test_unknown_scheme_and_unknown_space(self):
        for locator in ("sqlite:///tmp/x.db", "memory://nowhere", "markdown:///nonexistent"):
            self.assertEqual(ctx("validate", env={"CTX_STORE": locator}),
                             (2, "", f"NO_STORE {locator}: no store found\n"), locator)

    @unittest.skipUnless(os.pathsep == ":", "the list separator is ':' here")
    def test_a_list_keeps_scheme_locators_whole(self):
        self.assertEqual(backend.split_locators("/a/b:memory://one:/c:markdown:///d/e::x"),
                         ["/a/b", "memory://one", "/c", "markdown:///d/e", "x"])
        self.assertEqual(backend.split_locators(""), [])
        self.assertEqual(backend.split_locators("relative/store"), ["relative/store"])

    def test_a_list_mixes_backends(self):
        fixture_space("second")
        code, out, _ = run(("doctor", "--json"), os.pathsep.join([FIXTURE, "memory://second"]))
        self.assertEqual([s["backend"] for s in json.loads(out)["data"]["stores"]], ["markdown", "memory"])
        MemoryBackend.spaces["second"]["docs"]["reference/only-here"] = b"---\ntitle: Only here\ntype: reference\n---\n\nfound\n"
        locators = os.pathsep.join([FIXTURE, "memory://second"])
        self.assertEqual(run(("get", "reference/only-here"), locators), (0, "found\n", ""))
        self.assertEqual(run(("find", "only here"), locators)[1],
                         "1 hits\n2:reference/only-here · Only here · - · found\n")


if __name__ == "__main__":
    unittest.main()
