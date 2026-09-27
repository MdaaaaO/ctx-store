"""P1: validated, locked, audited writes and the cold-start read."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from tests.harness import CTX, FIXTURE, ctx, golden

NOW = "2026-01-08T09:30:00Z"
EPIC = "epics/sample-rollout"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.store = os.path.join(self.work.name, "store")
        shutil.copytree(FIXTURE, self.store)
        self.env = {"CTX_STORE": self.store, "CTX_ACTOR": "tester"}

    def run_ctx(self, *args, stdin=None, **env):
        return ctx("--now", NOW, *args, env={**self.env, **env}, stdin=stdin)

    def path(self, key):
        return os.path.join(self.store, key + ".md")

    def text(self, key):
        with open(self.path(key), encoding="utf-8") as handle:
            return handle.read()

    def put(self, key, text):
        os.makedirs(os.path.dirname(self.path(key)), exist_ok=True)
        with open(self.path(key), "w", encoding="utf-8") as handle:
            handle.write(text)

    def audit(self, actor="tester"):
        path = os.path.join(self.store, ".audit", actor + ".jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def fails(self, result, code, line):
        self.assertEqual((result[0], result[1], result[2]), (code, "", line + "\n"))


class Validate(StoreCase):
    def test_clean_store(self):
        self.assertEqual(self.run_ctx("validate"), (0, "ok: 6 docs checked\n", ""))

    def test_findings(self):
        self.put("epics/broken", (
            "---\ntitle: Broken\ntype: epic\nstatus: sleeping\ntags: nope\nupdated: yesterday\n---\n\n"
            "## Goal\nx\n\n## Goal\ny\n\n## Session log\n- 2026-01-05 — fine.\nnot an entry\n"
        ))
        self.put("reference/no-type", "---\ntitle: No type\n---\n\nbody\n")
        self.put("reference/nested", "---\ntitle: Nested\ntype: reference\nmeta:\n  deep: 1\n---\n")
        self.put("reference/no-frontmatter", "# Just a heading\n")
        code, out, err = self.run_ctx("validate", "--json")
        self.assertEqual(code, 3)
        self.assertEqual(err, golden("validate-findings.txt", err))
        found = json.loads(out)["error"]["findings"]
        self.assertEqual([f["doc"] for f in found], sorted(f["doc"] for f in found))
        self.assertEqual(found[0], {
            "code": "SCHEMA_VIOLATION", "detail": "heading Goal", "doc": "epics/broken",
            "message": "schema violation",
        })

    def test_generated_and_ignored_are_not_docs(self):
        self.put("INDEX", "no frontmatter, generated\n")
        self.put("README", "no frontmatter, ignored\n")
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_heading_in_fenced_code_is_not_a_heading(self):
        text = self.text(EPIC).replace("## Goal\n", "## Goal\n```\n## Goal\n```\n")
        self.put(EPIC, text)
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_changed(self):
        self.fails(self.run_ctx("validate", "--changed")[:3], 3, "\n".join(
            f"UNAUDITED_WRITE {key}: doc changed with no audit row" for key in (
                "epics/sample-rollout", "ledger", "reference/lock-modes",
                "sessions/alpha-rollout", "sessions/beta-docs", "sessions/gamma-old")))
        self.assertEqual(
            self.run_ctx("validate", "--changed", "--adopt"),
            (0, "ok: 6 docs checked, 6 adopted\n", ""))
        self.assertEqual(self.run_ctx("validate", "--changed"), (0, "ok: 0 docs checked\n", ""))
        self.assertEqual([row["verb"] for row in self.audit()], ["adopt"] * 6)

        self.put(EPIC, self.text(EPIC) + "- 2026-01-08 — by hand.\n")
        self.fails(self.run_ctx("validate", "--changed"), 3,
                   "UNAUDITED_WRITE epics/sample-rollout: doc changed with no audit row")
        self.assertEqual(self.run_ctx("log", EPIC, "through ctx")[0], 0)
        self.fails(self.run_ctx("validate", "--changed"), 3,
                   "UNAUDITED_WRITE epics/sample-rollout: doc changed with no audit row")
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)
        self.assertEqual(self.run_ctx("log", EPIC, "again")[0], 0)
        self.assertEqual(self.run_ctx("validate", "--changed"), (0, "ok: 0 docs checked\n", ""))

    def test_changed_reports_a_removed_doc(self):
        self.run_ctx("validate", "--changed", "--adopt")
        os.unlink(self.path("ledger"))
        self.fails(self.run_ctx("validate", "--changed"), 3,
                   "UNAUDITED_WRITE ledger: doc changed with no audit row")

    def test_adopt_keeps_an_invalid_doc_out(self):
        self.put("reference/no-type", "---\ntitle: No type\n---\n")
        code, _, err = self.run_ctx("validate", "--changed", "--adopt")
        self.assertEqual((code, err), (3, "SCHEMA_VIOLATION reference/no-type type: schema violation\n"))
        self.assertNotIn("reference/no-type", [row["doc"] for row in self.audit()])

    def test_adopt_needs_changed(self):
        self.fails(self.run_ctx("validate", "--adopt"), 1, "USAGE --adopt: bad command line")

    def test_changed_is_fast(self):
        for number in range(200):
            self.put(f"reference/doc-{number:03}", self.text("reference/lock-modes") + "x" * 15000)
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)
        took = []
        for _ in range(20):
            start = time.perf_counter()
            self.assertEqual(self.run_ctx("validate", "--changed")[0], 0)
            took.append(time.perf_counter() - start)
        p95 = sorted(took)[18]
        self.assertLess(p95, 0.3, f"p95 {p95:.3f}s over a 206-doc, 3 MB store")


class Log(StoreCase):
    def test_appends_in_order(self):
        before = self.text(EPIC)
        code, out, err = self.run_ctx("log", EPIC, "region three done.")
        self.assertEqual((code, out, err), (0, "logged: epics/sample-rollout\n", ""))
        self.assertEqual(self.text(EPIC), before + "- 2026-01-08 — region three done.\n")
        self.assertEqual(self.text(EPIC), golden("log-doc.md", self.text(EPIC)))
        row, = self.audit()
        self.assertEqual(sorted(row), ["actor", "after", "before", "doc", "seq", "ts", "verb"])
        self.assertEqual((row["ts"], row["actor"], row["verb"], row["doc"]), (NOW, "tester", "log", EPIC))
        self.assertNotEqual(row["before"], row["after"])
        self.assertIn("updated: 2026-01-07\n", self.text(EPIC))

    def test_json(self):
        code, out, _ = self.run_ctx("log", EPIC, "x", "--json", "--date", "2026-02-01")
        data = json.loads(out)
        self.assertEqual((code, data["verb"], data["data"]["line"]), (0, "log", "- 2026-02-01 — x"))

    def test_section_in_the_middle(self):
        self.put("reference/notes", "---\ntitle: N\ntype: reference\n---\n\n## Log\n- 2026-01-01 — a\n\n\n## After\nkept\n")
        self.assertEqual(self.run_ctx("log", "reference/notes", "--section", "Log", "b")[0], 0)
        self.assertEqual(self.text("reference/notes").split("---\n", 2)[2],
                         "\n## Log\n- 2026-01-01 — a\n- 2026-01-08 — b\n\n\n## After\nkept\n")

    def test_empty_section_and_no_final_newline(self):
        self.put("reference/notes", "---\ntitle: N\ntype: reference\n---\n\n## Log")
        self.assertEqual(self.run_ctx("log", "reference/notes", "--section", "Log", "first")[0], 0)
        self.assertTrue(self.text("reference/notes").endswith("## Log\n- 2026-01-08 — first\n"))

    def test_known_section_failure_modes(self):
        self.put("reference/notes", (
            "---\ntitle: N\ntype: reference\n---\n\n## Log #ops\n- 2026-01-01 — tagged\n\n"
            "## Plain\ntext\n"))
        # a heading with trailing tags is its whole text, never a prefix match
        self.fails(self.run_ctx("log", "reference/notes", "--section", "Log", "x"), 2,
                   "NO_SUCH_SECTION Log: no such section")
        self.assertEqual(self.run_ctx("log", "reference/notes", "--section", "Log #ops", "x")[0], 0)
        # an entry can never turn the line above it into a setext heading
        for text in ("---", "===", "--- ", "\n---"):
            self.run_ctx("log", "reference/notes", "--section", "Plain", text)
            for line in self.text("reference/notes").split("\n"):
                self.assertNotRegex(line, r"^(-{3,}|={3,})\s*$" if line != "---" else r"^$")
        # a parameter that is not the verb's is rejected, never ignored
        self.fails(self.run_ctx("log", "reference/notes", "x", "--sektion", "Plain"), 1,
                   "USAGE --sektion: bad command line")
        self.fails(self.run_ctx("log", "--stdin", stdin='{"doc": "reference/notes", "text": "x", "sektion": "Plain"}'),
                   1, "USAGE sektion: bad command line")

    def test_duplicate_heading_is_ambiguous(self):
        self.put("reference/notes", "---\ntitle: N\ntype: reference\n---\n\n## Log\n\n## Log\n")
        self.fails(self.run_ctx("log", "reference/notes", "--section", "Log", "x"), 3,
                   "AMBIGUOUS_SELECTOR Log: selector matches more than one target")

    def test_fuzzed_selectors(self):
        before = self.text(EPIC)
        for selector in ("session log", "Session log ", " Session log", "Session", "Session log\n", "## Session log",
                         "Session  log", "*", ".*", "", "Session log\0", "Séssion log", "Goal\n## Session log"):
            code = self.run_ctx("log", "--stdin", stdin=json.dumps({"doc": EPIC, "section": selector, "text": "x"}))[0]
            self.assertIn(code, (1, 2), repr(selector))
        self.assertEqual(self.text(EPIC), before)
        self.assertEqual(self.audit(), [])

    def test_shell_hostile_payloads(self):
        for number, text in enumerate(("it's \"quoted\" $HOME `id` $(id) \\n", "-- --json --section Goal", "- leading dash",
                                       "tab\there | pipe ; semi & amp > out", "ünïcødé — ✓", "<!-- comment -->")):
            for how in ("stdin", "from", "argv"):
                if how == "stdin":
                    result = self.run_ctx("log", "--stdin", stdin=json.dumps({"doc": EPIC, "text": text}))
                elif how == "from":
                    payload = os.path.join(self.work.name, "payload")
                    with open(payload, "w", encoding="utf-8") as handle:
                        handle.write(text + "\n")
                    result = self.run_ctx("log", EPIC, "--from", payload)
                else:
                    result = self.run_ctx("log", EPIC, "--", text)
                self.assertEqual(result[0], 0, (how, text, result))
                self.assertTrue(self.text(EPIC).endswith(f"- 2026-01-08 — {text}\n"), (how, text))
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_text_is_one_line(self):
        for text in ("two\nlines", "", "   ", "a\rb"):
            result = self.run_ctx("log", "--stdin", stdin=json.dumps({"doc": EPIC, "text": text}))
            self.assertEqual((result[0], result[2]), (3, "SCHEMA_VIOLATION text: schema violation\n"), repr(text))

    def test_newest_first(self):
        schema = os.path.join(self.store, ".ctx", "types", "epic.json")
        with open(schema) as handle:
            rules = json.load(handle)
        rules["log"]["order"] = "newest-first"
        with open(schema, "w") as handle:
            json.dump(rules, handle)
        self.put(EPIC, self.text(EPIC).replace("## Session log\n", "## Session log\n<!-- Dated one-liners,\n     newest first. -->\n\n"))
        self.assertEqual(self.run_ctx("log", EPIC, "newest")[0], 0)
        self.assertTrue(self.text(EPIC).endswith(
            "## Session log\n<!-- Dated one-liners,\n     newest first. -->\n\n"
            "- 2026-01-08 — newest\n- 2026-01-05 — created.\n- 2026-01-06 — region one done.\n"
            "- 2026-01-07 — region two done.\n"))
        self.assertIn("Session log (last 4 of 4):\n- 2026-01-08 — newest\n", self.run_ctx("brief", EPIC)[1])
        self.put("epics/empty", self.text(EPIC).split("## Session log")[0] + "## Session log\n<!-- none yet -->\n")
        self.assertEqual(self.run_ctx("log", "epics/empty", "first")[0], 0)
        self.assertTrue(self.text("epics/empty").endswith("## Session log\n<!-- none yet -->\n- 2026-01-08 — first\n"))
        rules["log"]["order"] = "sideways"
        with open(schema, "w") as handle:
            json.dump(rules, handle)
        self.fails(self.run_ctx("log", EPIC, "x"), 3, "SCHEMA_VIOLATION log.order: schema violation")

    def test_ledger(self):
        self.assertEqual(self.run_ctx("log", "ledger", "| 2026-01-08 | appended |")[0], 0)
        self.assertTrue(self.text("ledger").endswith("| 2026-01-05 | opened |\n| 2026-01-08 | appended |\n"))

    def test_not_found(self):
        self.fails(self.run_ctx("log", "epics/none", "x"), 2, "NO_SUCH_DOC epics/none: no such doc")
        self.fails(self.run_ctx("log", "reference/lock-modes", "x"), 1, "USAGE --section: bad command line")
        self.fails(self.run_ctx("log", EPIC), 1, "USAGE text: bad command line")

    def test_path_escape(self):
        outside = os.path.join(self.work.name, "outside.md")
        with open(outside, "w") as handle:
            handle.write("---\ntitle: O\ntype: reference\n---\n\n## Log\n")
        os.symlink(outside, self.path("reference/link"))
        os.symlink(self.work.name, os.path.join(self.store, "reference", "up"))
        for key in ("../outside", "reference/../../outside", outside, "reference/link", "reference/up/outside",
                    ".audit/tester", ".ctx/types/epic"):
            result = self.run_ctx("log", "--stdin", stdin=json.dumps({"doc": key, "section": "Log", "text": "x"}))
            self.assertEqual(result[0], 3, key)
            if True:
                self.assertEqual(result[2], f"PATH_ESCAPE {key}: path leaves the store\n")
        with open(outside) as handle:
            self.assertNotIn("x", handle.read().split("## Log")[1])

    def test_secret_guard(self):
        before = self.text(EPIC)
        secrets = {
            "aws-access-key": "key AKIA" + "ABCDEFGHIJKLMNOP",
            "github-token": "ghp_" + "a" * 36,
            "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----",
            "slack-token": "xoxb-" + "1234567890-abcdefghijkl",
            "api-key": "sk-" + "a1B2" * 10,
            "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
        }
        for rule, text in secrets.items():
            result = self.run_ctx("log", EPIC, "--", text)
            self.fails(result, 3, f"SECRET_DETECTED {rule}: payload looks like a secret")
            self.assertNotIn(text, result[2])
        self.assertEqual(self.text(EPIC), before)
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.run_ctx("log", EPIC, "the token was rotated, see the vault")[0], 0)

    def test_generated(self):
        self.put("INDEX", "---\ntitle: I\ntype: reference\n---\n\n## Log\n")
        self.fails(self.run_ctx("log", "INDEX", "--section", "Log", "x"), 3, "GENERATED INDEX: doc is generated")

    def test_a_write_needs_a_named_store(self):
        before = self.text(EPIC)
        for verb in (("log", EPIC, "x"), ("fm", EPIC, "status", "done"), ("touch", "--session", "sid-alpha"),
                     ("validate", "--changed", "--adopt")):
            result = ctx(*verb, env={"CTX_ACTOR": "tester"}, cwd=os.path.join(self.store, "epics"), walk=True)
            self.fails(result, 5, "STORE_NOT_NAMED: a write needs CTX_STORE or --store")
        self.assertEqual(self.text(EPIC), before)
        self.assertFalse(os.path.exists(os.path.join(self.store, ".audit")))
        for read in (("brief", EPIC), ("validate",)):
            self.assertEqual(ctx(*read, cwd=os.path.join(self.store, "epics"), walk=True)[0], 0)
        self.assertEqual(ctx("log", EPIC, "x", "--store", self.store, env={"CTX_ACTOR": "tester"})[0], 0)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores modes")
    def test_unwritable_store(self):
        before = self.text(EPIC)
        os.chmod(self.store, stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = self.run_ctx("log", EPIC, "x")
            self.assertEqual(self.run_ctx("brief", EPIC)[0], 0)
            self.assertEqual(self.run_ctx("validate")[0], 0)
        finally:
            os.chmod(self.store, stat.S_IRWXU)
        self.fails(result, 5, f"STORE_READONLY {self.store}: store is read-only or unwritable")
        self.assertEqual(self.text(EPIC), before)

    @unittest.skipUnless(os.environ.get("CTX_TEST_RO_STORE"), "needs a read-only mount")
    def test_read_only_mount(self):
        store = os.environ["CTX_TEST_RO_STORE"]
        env = {"CTX_STORE": store, "CTX_ACTOR": "tester"}
        self.assertEqual(ctx("log", EPIC, "x", env=env)[0], 5)
        self.assertEqual(ctx("validate", "--changed", "--adopt", env=env)[0], 5)
        self.assertEqual(ctx("validate", env=env)[0], 0)
        self.assertEqual(ctx("brief", "--registry", env=env)[0], 0)

    def test_no_temp_file_is_left(self):
        self.run_ctx("log", EPIC, "x")
        self.run_ctx("log", EPIC, "--", "ghp_" + "a" * 36)
        left = [name for _, _, files in os.walk(self.store) for name in files if name.startswith(".ctx-tmp")]
        self.assertEqual(left, [])


class Locks(StoreCase):
    def race(self, mode):
        script = (
            "import subprocess, sys\n"
            "for n in range(50):\n"
            "    subprocess.run([sys.executable, sys.argv[1], 'log', sys.argv[2], f'{sys.argv[3]} {n}'], check=True)\n")
        env = {**self.env, "CTX_NO_WALK": "1", "CTX_LOCK_MODE": mode, "CTX_LOCK_TIMEOUT": "60"}
        writers = [
            subprocess.Popen([sys.executable, "-c", script, CTX, EPIC, name], env={**env, "CTX_ACTOR": name})
            for name in ("one", "two")
        ]
        self.assertEqual([writer.wait(timeout=120) for writer in writers], [0, 0])
        lines = self.text(EPIC).split("\n")
        for name in ("one", "two"):
            kept = [line for line in lines if f"— {name} " in line]
            self.assertEqual(len(kept), 50, mode)
            self.assertEqual([int(line.rsplit(" ", 1)[1]) for line in kept], list(range(50)))
            self.assertEqual(len(self.audit(name)), 50)
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(os.listdir(os.path.join(self.store, ".lock")), ["store.lock"] if mode == "flock" else [])

    def test_two_writers_flock(self):
        self.race("flock")

    def test_two_writers_mkdir(self):
        self.race("mkdir")

    def test_timeout(self):
        held = os.path.join(self.store, ".lock", "store.d")
        os.makedirs(held)
        with open(os.path.join(held, "owner"), "w") as handle:
            handle.write(f"{os.getpid()}\n")
        start = time.perf_counter()
        result = self.run_ctx("log", EPIC, "x", CTX_LOCK_MODE="mkdir", CTX_LOCK_TIMEOUT="0.3")
        self.fails(result, 4, "LOCK_TIMEOUT store: lock timeout")
        self.assertLess(time.perf_counter() - start, 5)

    def test_stale_mkdir_lock_is_broken(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        held = os.path.join(self.store, ".lock", "store.d")
        os.makedirs(held)
        with open(os.path.join(held, "owner"), "w") as handle:
            handle.write(f"{gone.pid}\n")
        self.assertEqual(self.run_ctx("log", EPIC, "x", CTX_LOCK_MODE="mkdir", CTX_LOCK_TIMEOUT="5")[0], 0)

    def test_bad_lock_mode(self):
        self.fails(self.run_ctx("log", EPIC, "x", CTX_LOCK_MODE="hope"), 1, "USAGE CTX_LOCK_MODE: bad command line")


class Frontmatter(StoreCase):
    def test_sets_one_line(self):
        before = self.text(EPIC)
        self.assertEqual(self.run_ctx("fm", EPIC, "status", "paused"), (0, "set: epics/sample-rollout status\n", ""))
        expected = before.replace("status: active", "status: paused").replace("updated: 2026-01-07", "updated: 2026-01-08")
        self.assertEqual(self.text(EPIC), expected)
        self.assertEqual(self.audit()[0]["verb"], "fm")

    def test_new_field_and_list(self):
        self.assertEqual(self.run_ctx("fm", EPIC, "owner", "team a")[0], 0)
        self.assertEqual(self.run_ctx("fm", EPIC, "tags", "sample, rollout")[0], 0)
        self.assertEqual(self.run_ctx("fm", EPIC, "tags", "[one]", "--json")[0], 0)
        text = self.text(EPIC)
        self.assertIn("tags: [one]\n", text)
        self.assertIn("updated: 2026-01-08\nowner: team a\n---\n", text)

    def test_schema_check(self):
        before = self.text(EPIC)
        for field, value in (("status", "sleeping"), ("updated", "soon"), ("title", "")):
            self.fails(self.run_ctx("fm", EPIC, field, value), 3, f"SCHEMA_VIOLATION {field}: schema violation")
        self.fails(self.run_ctx("fm", "--stdin", stdin=json.dumps({"doc": EPIC, "field": "title", "value": "a\nb"})),
                   3, "SCHEMA_VIOLATION title: schema violation")
        self.fails(self.run_ctx("fm", EPIC, "bad key", "x"), 1, "USAGE bad key: bad command line")
        self.assertEqual(self.text(EPIC), before)


class Touch(StoreCase):
    def test_by_harness_id_and_by_name(self):
        self.assertEqual(self.run_ctx("touch", "--session", "sid-alpha"), (0, "touched: sessions/alpha-rollout\n", ""))
        self.assertIn("heartbeat: 2026-01-08T09:30:00Z\n", self.text("sessions/alpha-rollout"))
        self.assertEqual(self.run_ctx("touch", "--session", "beta-docs", "--working", "review round 2")[0], 0)
        text = self.text("sessions/beta-docs")
        self.assertIn("working_on: review round 2\n", text)
        self.assertIn("heartbeat: 2026-01-08T09:30:00Z\n", text)
        self.assertEqual(self.audit("alpha-rollout")[0]["actor"], "alpha-rollout")
        self.assertEqual(self.audit(), [])

    def test_selector(self):
        self.fails(self.run_ctx("touch", "--session", "nobody"), 2, "NO_SUCH_DOC nobody: no such doc")
        self.put("sessions/alpha-twin", self.text("sessions/alpha-rollout").replace("session: alpha-rollout", "session: twin"))
        self.fails(self.run_ctx("touch", "--session", "sid-alpha"), 3,
                   "AMBIGUOUS_SELECTOR sid-alpha: selector matches more than one target")
        self.fails(self.run_ctx("touch"), 1, "USAGE session: bad command line")

    def test_only_the_owner_writes_a_session_doc(self):
        self.fails(self.run_ctx("fm", "sessions/alpha-rollout", "status", "ended"), 3,
                   "NOT_OWNER sessions/alpha-rollout: doc is owned by another actor")
        self.assertEqual(self.run_ctx("fm", "sessions/alpha-rollout", "status", "ended", CTX_ACTOR="alpha-rollout")[0], 0)


class Brief(StoreCase):
    def test_goldens(self):
        for name, args in (("brief-doc.txt", (EPIC,)), ("brief-registry.txt", ("--registry",)),
                           ("brief-session.txt", ("--session", "sid-alpha"))):
            code, out, err = self.run_ctx("brief", *args)
            self.assertEqual((code, err), (0, ""), name)
            self.assertEqual(out, golden(name, out))

    def test_budget(self):
        for number in range(40):
            self.run_ctx("log", EPIC, f"entry {number} " + "x" * 200)
        full = self.run_ctx("brief", EPIC)[1]
        for budget in (1, 40, 64, 100, 300, 1000, 4096):
            code, out, _ = self.run_ctx("brief", EPIC, "--budget", str(budget), "--json")
            data = json.loads(out)["data"]
            self.assertEqual(code, 0)
            self.assertLessEqual(len(data["text"].encode()) + 1, max(budget, 40), budget)
            self.assertEqual(data["bytes"], len(data["text"].encode()) + 1)
            self.assertEqual(data["truncated"], data["text"] + "\n" != full, budget)
            if data["truncated"]:
                self.assertRegex(data["text"].split("\n")[-1], r"^… \d+ more lines, raise --budget$")

    def test_over_8_kb_needs_full(self):
        self.put("sessions/big", self.text("sessions/alpha-rollout").replace("alpha", "big") + "line of text\n" * 2000)
        capped = self.run_ctx("brief", "--session", "sid-big", "--budget", "20000")[1]
        self.assertLessEqual(len(capped.encode()), 8192)
        full = self.run_ctx("brief", "--session", "sid-big", "--budget", "20000", "--full")[1]
        self.assertGreater(len(full.encode()), 8192)
        self.assertLessEqual(len(full.encode()), 20000)

    def test_usage(self):
        self.fails(self.run_ctx("brief"), 1, "USAGE brief: bad command line")
        self.fails(self.run_ctx("brief", EPIC, "--registry"), 1, "USAGE brief: bad command line")
        self.fails(self.run_ctx("brief", EPIC, "--budget", "lots"), 1, "USAGE --budget: bad command line")
        self.fails(self.run_ctx("brief", EPIC, "--budget", "0"), 1, "USAGE --budget: bad command line")
        self.fails(self.run_ctx("brief", "epics/none"), 2, "NO_SUCH_DOC epics/none: no such doc")

    def test_reads_write_nothing(self):
        before = sorted(os.walk(self.store))
        for args in ((EPIC,), ("--registry",), ("--session", "sid-alpha")):
            self.run_ctx("brief", *args)
        self.run_ctx("validate")
        self.run_ctx("validate", "--changed")
        self.assertEqual(sorted(os.walk(self.store)), before)


if __name__ == "__main__":
    unittest.main()
