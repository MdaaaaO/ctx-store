"""P3: schema versions, migrate, maintain."""
import json
import os
import shutil
import subprocess

from tests.harness import golden
from tests.test_store import EPIC, NOW, StoreCase

EPIC_V2 = {
    "version": 2,
    "migrations": [
        {"to": 1, "log_order": "oldest-first",
         "replace_comments": [{"old": "newest first", "new": "oldest first"}]},
        {"to": 2, "rename_fields": {"status": "state"}, "rename_sections": {"Remaining work": "Open work"},
         "set_fields": {"owner": "unassigned"}, "remove_fields": ["tags"]},
    ],
    "frontmatter": {
        "title": {"required": True},
        "type": {"const": "epic"},
        "state": {"required": True, "enum": ["active", "paused", "done"]},
        "owner": {"required": True},
        "updated": {"kind": "date"},
    },
    "sections": ["Goal", "Open work", "Session log"],
    "log": {"section": "Session log", "grammar": "^- \\d{4}-\\d{2}-\\d{2} — .+$"},
}
NEWEST_FIRST = (
    "---\ntitle: Old style\ntype: epic\nstatus: paused\ntags: [a, b]\nupdated: 2026-01-03\n---\n\n# Old style\n\n"
    "## Goal\nKeep the prose as it is: newest first stays in a sentence.\n\n## Remaining work\n- one\n\n"
    "## Session log\n<!-- Dated one-liners, newest first. -->\n- 2026-01-03 — third.\n- 2026-01-02 — second.\n"
    "- 2026-01-01 — first.\n"
)


class UpkeepCase(StoreCase):
    def schema(self, name, rules):
        with open(os.path.join(self.store, ".ctx", "types", name + ".json"), "w") as handle:
            json.dump(rules, handle)

    def settings(self, **more):
        path = os.path.join(self.store, "ctx-store.json")
        with open(path) as handle:
            data = json.load(handle)
        data.update(more)
        with open(path, "w") as handle:
            json.dump(data, handle)

    def big_log(self, order=None):
        if order:
            with open(os.path.join(self.store, ".ctx", "types", "epic.json")) as handle:
                rules = json.load(handle)
            rules["log"]["order"] = order
            self.schema("epic", rules)
        entries = [f"- 2026-01-{day:02} — entry {day} " + "x" * 900 for day in range(1, 41)]
        if order == "newest-first":
            entries.reverse()
        self.put(EPIC, self.text(EPIC).split("## Session log\n")[0] + "## Session log\n<!-- log -->\n" + "\n".join(entries) + "\n")
        return entries


class Versions(UpkeepCase):
    def test_a_type_without_versions_needs_no_stamp(self):
        self.assertEqual(self.run_ctx("migrate", "--check"), (0, "ok: nothing to migrate\n", ""))
        self.assertEqual(self.run_ctx("fm", EPIC, "status", "paused")[0], 0)
        self.assertNotIn("schema_version", self.text(EPIC))

    def test_writes_stamp_the_version(self):
        rules = {"version": 1, "migrations": [{"to": 1}], "frontmatter": {"title": {"required": True}}}
        self.schema("reference", rules)
        self.assertEqual(self.run_ctx("new", "reference", "reference/fresh")[0], 0)
        self.assertIn("schema_version: reference.v1\n", self.text("reference/fresh"))
        self.assertEqual(self.run_ctx("create", "reference/other", "--type", "reference")[0], 0)
        self.assertIn("schema_version: reference.v1\n", self.text("reference/other"))
        self.assertEqual(self.run_ctx("fm", "reference/fresh", "title", "Fresh")[0], 0)
        self.assertEqual(self.text("reference/fresh").count("schema_version"), 1)

    def test_a_doc_behind_its_type_is_pending(self):
        self.schema("reference", {"version": 1, "migrations": [{"to": 1}]})
        self.fails(self.run_ctx("validate"), 3,
                   "MIGRATION_PENDING reference/lock-modes: doc is behind its type's schema version")
        self.fails(self.run_ctx("fm", "reference/lock-modes", "title", "x"), 3,
                   "MIGRATION_PENDING reference/lock-modes: doc is behind its type's schema version")
        self.assertEqual(self.run_ctx("brief", "reference/lock-modes")[0], 0)

    def test_a_stamp_that_is_not_the_types(self):
        self.schema("reference", {"version": 1, "migrations": [{"to": 1}]})
        for stamp in ("reference.v2", "epic.v1", "v1", "reference.v", "[reference.v1]"):
            self.put("reference/lock-modes", self.text("reference/lock-modes").replace(
                "type: reference\n", f"type: reference\nschema_version: {stamp}\n") if "schema_version" not in
                self.text("reference/lock-modes") else
                "\n".join(f"schema_version: {stamp}" if line.startswith("schema_version") else line
                          for line in self.text("reference/lock-modes").split("\n")))
            self.fails(self.run_ctx("validate"), 3, "SCHEMA_VIOLATION reference/lock-modes schema_version: schema violation")

    def test_a_wrong_stamp_is_not_written_over(self):
        self.schema("reference", {"version": 1, "migrations": [{"to": 1}]})
        wrong = self.text("reference/lock-modes").replace("type: reference\n", "type: reference\nschema_version: reference.v7\n")
        self.put("reference/lock-modes", wrong)
        self.put("reference/links", "---\ntitle: L\ntype: reference\nschema_version: reference.v1\n---\n\n[[lock-modes]]\n")
        for verb in (("fm", "reference/lock-modes", "title", "x"), ("str_replace", "reference/lock-modes", "--old", "Lock", "--new", "x"),
                     ("log", "reference/lock-modes", "--section", "Summary", "x"), ("delete", "reference/lock-modes"),
                     ("rename", "reference/lock-modes", "reference/locks"), ("migrate", "--apply")):
            result = self.run_ctx(*verb)
            if verb[0] == "migrate":
                self.assertEqual(result[0], 0)
            else:
                self.fails(result, 3, "SCHEMA_VIOLATION schema_version: schema violation")
        self.assertEqual(self.text("reference/lock-modes"), wrong)
        self.assertEqual(self.run_ctx("fm", "reference/lock-modes", "schema_version", "reference.v1")[0], 0)
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_a_broken_migration_list_is_one_error_line(self):
        for rules in ({"version": 2, "migrations": [{"to": 1}]}, {"version": 1, "migrations": [{"to": 2}]},
                      {"version": 1, "migrations": [{"to": 1, "rewrite_prose": True}]}, {"version": -1},
                      {"version": 1, "migrations": [{"to": 1, "log_order": "sideways"}]},
                      {"version": 1, "migrations": [{"to": 1, "log_order_from": "newest-first"}]},
                      {"version": 1, "migrations": [{"to": 1, "log_order": "oldest-first",
                                                     "log_order_from": "oldest-first"}]},
                      {"version": 1, "migrations": [{"to": 1, "log_order": "oldest-first",
                                                     "log_order_from": ["newest-first"]}]},
                      {"version": 1, "migrations": [{"to": 1, "replace_comments": [{"old": "", "new": "x"}]}]},
                      {"version": 1, "migrations": [{"to": 1, "rename_fields": ["a"]}]}, {"version": "1"}):
            self.schema("epic", rules)
            for verb in (("validate",), ("migrate", "--check"), ("migrate", "--apply")):
                self.fails(self.run_ctx(*verb), 3, "SCHEMA_VIOLATION .ctx/types/epic.json: schema violation")


class Migrate(UpkeepCase):
    def setUp(self):
        super().setUp()
        self.put("epics/old-style", NEWEST_FIRST)
        self.put("epics/half-way", NEWEST_FIRST.replace("type: epic\n", "type: epic\nschema_version: epic.v1\n"))
        self.schema("epic", EPIC_V2)

    def test_check(self):
        self.fails(self.run_ctx("migrate", "--check"), 3, "\n".join(
            f"MIGRATION_PENDING {key}: doc is behind its type's schema version"
            for key in ("epics/half-way", "epics/old-style", "epics/sample-rollout")))
        code, out, _ = self.run_ctx("migrate", "--check", "--json")
        self.assertEqual([f["doc"] for f in json.loads(out)["error"]["findings"]],
                         ["epics/half-way", "epics/old-style", "epics/sample-rollout"])

    def test_dry_run_writes_nothing(self):
        before = sorted(os.walk(self.store))
        code, out, err = self.run_ctx("migrate", "--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, golden("migrate-dry-run.txt", out))
        self.assertEqual(sorted(os.walk(self.store)), before)
        self.assertEqual(self.text("epics/old-style"), NEWEST_FIRST)

    def test_apply(self):
        code, out, err = self.run_ctx("migrate", "--apply")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("3 docs migrated\n"))
        self.assertEqual(self.text("epics/old-style"), golden("migrated-epic.md", self.text("epics/old-style")))
        text = self.text("epics/half-way")
        self.assertIn("- 2026-01-03 — third.\n- 2026-01-02 — second.\n- 2026-01-01 — first.\n", text)
        self.assertIn("<!-- Dated one-liners, newest first. -->", text)
        self.assertIn("schema_version: epic.v2\n", text)
        self.assertIn("## Open work\n", text)
        self.assertIn("- 2026-01-05 — created.\n- 2026-01-06 — region one done.\n- 2026-01-07 — region two done.\n",
                      self.text(EPIC))
        self.assertEqual(self.run_ctx("validate"), (0, "ok: 8 docs checked\n", ""))
        self.assertEqual(self.run_ctx("migrate", "--check"), (0, "ok: nothing to migrate\n", ""))
        self.assertEqual([(row["verb"], row["doc"]) for row in self.audit()], [
            ("migrate", "epics/half-way"), ("migrate", "epics/old-style"), ("migrate", "epics/sample-rollout")])
        before = {key: self.text(key) for key in ("epics/old-style", "epics/half-way", EPIC)}
        self.assertEqual(self.run_ctx("migrate", "--apply"), (0, "0 docs migrated\n", ""))
        self.assertEqual({key: self.text(key) for key in before}, before)
        self.assertEqual(len(self.audit()), 3)
        self.assertEqual(self.run_ctx("log", "epics/old-style", "after the migration")[0], 0)
        self.assertTrue(self.text("epics/old-style").endswith("- 2026-01-03 — third.\n- 2026-01-08 — after the migration\n"))

    def test_a_declared_source_order_reverses_a_log_of_one_day(self):
        """Entries that all share a date read in either order; `log_order_from`
        says which one they were written in. A log whose dates already read
        in the target order is still left alone."""
        one_day = NEWEST_FIRST.replace("2026-01-02", "2026-01-03").replace("2026-01-01", "2026-01-03")
        self.put("epics/one-day", one_day)
        self.put("epics/old-style", NEWEST_FIRST.replace(
            "- 2026-01-03 — third.\n- 2026-01-02 — second.\n- 2026-01-01 — first.\n",
            "- 2026-01-01 — first.\n- 2026-01-03 — second.\n- 2026-01-03 — third.\n"))
        steps = [{**step, "log_order_from": "newest-first"} if step["to"] == 1 else step
                 for step in EPIC_V2["migrations"]]
        for source, expect in ((None, "third"), ("newest-first", "first")):
            with self.subTest(source=source):
                self.put("epics/one-day", one_day)
                self.schema("epic", {**EPIC_V2, "migrations": steps if source else EPIC_V2["migrations"]})
                self.assertEqual(self.run_ctx("migrate", "--apply")[0], 0)
                log = [line for line in self.text("epics/one-day").split("\n") if line.startswith("- 2026")]
                self.assertTrue(log[0].endswith(f" {expect}."), log)
                self.assertIn("- 2026-01-01 — first.\n- 2026-01-03 — second.\n- 2026-01-03 — third.\n",
                              self.text("epics/old-style"))  # already oldest-first: left alone
                for key in ("epics/one-day", "epics/old-style", "epics/half-way", EPIC):
                    self.put(key, self.text(key).replace("schema_version: epic.v2\n", ""))

    def test_a_doc_that_is_not_valid_after_its_steps_stops_the_run(self):
        """The run names every doc that blocks it, as `validate` does, and
        writes none: not even the docs ahead of it in key order."""
        self.put("epics/old-style", NEWEST_FIRST.replace("status: paused", "status: asleep"))
        self.put("epics/two-bad", NEWEST_FIRST.replace("status: paused", "status: someday").replace("title: Old style\n", ""))
        before = {key: self.text(key) for key in ("epics/half-way", "epics/old-style", "epics/two-bad", EPIC)}
        self.fails(self.run_ctx("migrate", "--apply"), 3, "\n".join((
            "SCHEMA_VIOLATION epics/old-style state: schema violation",
            "SCHEMA_VIOLATION epics/two-bad title: schema violation",
            "SCHEMA_VIOLATION epics/two-bad state: schema violation")))
        code, out, _ = self.run_ctx("migrate", "--apply", "--json")
        self.assertEqual(code, 3)
        self.assertEqual([(f["code"], f["doc"], f["detail"]) for f in json.loads(out)["error"]["findings"]], [
            ("SCHEMA_VIOLATION", "epics/old-style", "state"), ("SCHEMA_VIOLATION", "epics/two-bad", "title"),
            ("SCHEMA_VIOLATION", "epics/two-bad", "state")])
        self.assertEqual({key: self.text(key) for key in before}, before)
        self.assertEqual(self.audit(), [])

    def test_usage_and_named_store(self):
        self.fails(self.run_ctx("migrate"), 1, "USAGE migrate: bad command line")
        self.fails(self.run_ctx("migrate", "--check", "--apply"), 1, "USAGE migrate: bad command line")
        from tests.harness import ctx
        inside = os.path.join(self.store, "epics")
        self.assertEqual(ctx("migrate", "--check", cwd=inside, walk=True)[0], 3)
        self.assertEqual(ctx("migrate", "--dry-run", cwd=inside, walk=True)[0], 0)
        self.fails(ctx("migrate", "--apply", cwd=inside, walk=True, env={"CTX_ACTOR": "tester"}), 5,
                   "STORE_NOT_NAMED: a write needs CTX_STORE or --store")


NOTE_V1 = {
    "version": 1,
    "migrations": [{"to": 1, "log_order": "oldest-first"}],
    "frontmatter": {"title": {"required": True}, "type": {"const": "note"}},
}
NOTE_BODY = (
    "---\ntitle: Notes\ntype: note\nupdated: 2026-01-01\n---\n\n# Notes\n\n"
    "Some intro prose about this note, never touched.\n\n"
    "<!-- newest first -->\n"
    "- 2026-01-03 — third.\n- 2026-01-02 — second.\n- 2026-01-01 — first.\n\n"
    "## Related\n- something else entirely, not a log line.\n"
)


class SectionlessMigrate(UpkeepCase):
    """`log_order` also reorders the body-level dated list of a type with no
    `log.section`; prose, comments and anything past the first `##` stay put."""

    def setUp(self):
        super().setUp()
        self.put("notes/plain", NOTE_BODY)
        self.schema("note", NOTE_V1)

    def test_reorders_the_body_level_list_and_leaves_prose_and_comments(self):
        self.assertEqual(self.run_ctx("migrate", "--apply")[0], 0)
        text = self.text("notes/plain")
        self.assertIn(
            "# Notes\n\nSome intro prose about this note, never touched.\n\n"
            "<!-- newest first -->\n"
            "- 2026-01-01 — first.\n- 2026-01-02 — second.\n- 2026-01-03 — third.\n\n"
            "## Related\n- something else entirely, not a log line.\n",
            text)
        self.assertIn("schema_version: note.v1\n", text)

    def test_a_second_run_changes_nothing(self):
        self.assertEqual(self.run_ctx("migrate", "--apply")[0], 0)
        before = self.text("notes/plain")
        self.assertEqual(self.run_ctx("migrate", "--apply"), (0, "0 docs migrated\n", ""))
        self.assertEqual(self.text("notes/plain"), before)

    def test_a_declared_source_order_reverses_a_log_of_one_day(self):
        one_day = NOTE_BODY.replace("2026-01-03", "2026-01-01").replace("2026-01-02", "2026-01-01")
        for source, expect in ((None, "third"), ("newest-first", "first")):
            with self.subTest(source=source):
                self.put("notes/plain", one_day)
                steps = [{**NOTE_V1["migrations"][0], "log_order_from": source}] if source else NOTE_V1["migrations"]
                self.schema("note", {**NOTE_V1, "migrations": steps})
                self.assertEqual(self.run_ctx("migrate", "--apply")[0], 0)
                log = [line for line in self.text("notes/plain").split("\n") if line.startswith("- 2026")]
                self.assertTrue(log[0].endswith(f" {expect}."), log)


class SizeGuard(UpkeepCase):
    def test_a_large_doc_is_a_warning(self):
        self.put("reference/big", "---\ntitle: Big\ntype: reference\n---\n\n" + "a line of text\n" * 2200)
        code, out, err = self.run_ctx("validate")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "ok: 7 docs checked\nwarning: SIZE_GUARD reference/big: 33036 bytes\n")
        data = json.loads(self.run_ctx("validate", "--json")[1])["data"]
        self.assertEqual(data["warnings"], [{"code": "SIZE_GUARD", "doc": "reference/big", "bytes": 33036}])
        self.settings(maintain={"size_guard": 100000})
        self.assertEqual(self.run_ctx("validate")[1], "ok: 7 docs checked\n")


class Maintain(UpkeepCase):
    def test_nothing_to_do(self):
        before = sorted((folder, sorted(files)) for folder, _, files in os.walk(self.store))
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
        self.assertEqual(sorted((folder, sorted(files)) for folder, _, files in os.walk(self.store)), before)

    def test_log_tail_moves_to_the_archive(self):
        for order in (None, "newest-first"):
            with self.subTest(order=order):
                if os.path.exists(self.path("archive/sample-rollout-log")):
                    os.unlink(self.path("archive/sample-rollout-log"))
                entries = self.big_log(order)
                oldest_first = entries if order is None else list(reversed(entries))
                self.assertEqual(self.run_ctx("maintain")[1],
                                 "archived: 20 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
                kept = [line for line in self.text(EPIC).split("\n") if line.startswith("- 2026-01-")]
                self.assertEqual(kept, [e for e in entries if e in oldest_first[20:]])
                self.assertIn("## Session log\n<!-- log -->\n", self.text(EPIC))
                archive = self.text("archive/sample-rollout-log")
                self.assertTrue(archive.startswith("---\ntitle: Log of epics/sample-rollout\ntype: log\nupdated: 2026-01-08\n---\n"))
                self.assertEqual([l for l in archive.split("\n") if l.startswith("- ")], oldest_first[:20])
                self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
                self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_the_archive_doc_carries_its_types_version(self):
        self.schema("log", {"version": 2, "migrations": [{"to": 1}, {"to": 2}], "sections": ["Log"]})
        self.big_log()
        self.assertEqual(self.run_ctx("maintain")[0], 0)
        self.assertIn("schema_version: log.v2\n", self.text("archive/sample-rollout-log"))
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))

    def test_a_second_tail_is_appended(self):
        self.big_log()
        self.run_ctx("maintain")
        for day in range(41, 60):
            self.assertEqual(self.run_ctx("log", EPIC, f"entry {day} " + "y" * 900, "--date", f"2026-02-{day - 40:02}")[0], 0)
        self.assertEqual(self.run_ctx("maintain")[1],
                         "archived: 19 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
        archived = [l for l in self.text("archive/sample-rollout-log").split("\n") if l.startswith("- ")]
        self.assertEqual(len(archived), 39)
        self.assertEqual(archived, sorted(archived))
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)

    def test_ended_sessions_are_swept_with_their_audit_rows(self):
        self.assertEqual(self.run_ctx("touch", "--session", "sid-gamma")[0], 0)
        self.put("reference/links", "---\ntitle: L\ntype: reference\n---\n\nSee [[gamma-old]] and [beta](../sessions/beta-docs.md).\n")
        later = "2026-02-01T00:00:00Z"
        from tests.harness import ctx
        code, out, err = ctx("--now", later, "maintain", env=self.env)
        self.assertEqual((code, out, err), (0, "swept: sessions/gamma-old → sessions/archive/gamma-old\n", ""))
        self.assertFalse(os.path.exists(self.path("sessions/gamma-old")))
        self.assertIn("status: ended", self.text("sessions/archive/gamma-old"))
        self.assertIn("[[gamma-old]]", self.text("reference/links"))
        self.assertTrue(os.path.exists(os.path.join(self.store, ".audit", "archive", "gamma-old.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.store, ".audit", "gamma-old.jsonl")))
        self.assertTrue(os.path.exists(self.path("sessions/alpha-rollout")))
        self.assertEqual(ctx("--now", later, "maintain", env=self.env), (0, "ok: nothing to do\n", ""))
        self.assertEqual(self.run_ctx("brief", "--registry")[1].split("\n")[0], "sessions: 2 not ended")
        self.assertEqual(ctx("validate", "--changed", "--adopt", env=self.env)[0], 0)
        self.assertEqual(ctx("validate", "--changed", env=self.env), (0, "ok: 0 docs checked\n", ""))

    def test_a_recent_ended_session_stays(self):
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
        self.assertTrue(os.path.exists(self.path("sessions/gamma-old")))

    def test_catalog(self):
        self.settings(maintain={"catalog": "INDEX"})
        self.assertEqual(self.run_ctx("maintain"), (0, "catalog: INDEX (6 docs)\n", ""))
        self.assertEqual(self.text("INDEX"), golden("catalog.md", self.text("INDEX")))
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
        self.assertEqual(self.run_ctx("new", "reference", "reference/fresh")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "catalog: INDEX (7 docs)\n", ""))
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)
        self.settings(maintain={"catalog": "reference/lock-modes"})
        self.fails(self.run_ctx("maintain"), 3, "SCHEMA_VIOLATION maintain.catalog: schema violation")

    def test_named_store_and_read_only(self):
        from tests.harness import ctx
        self.fails(ctx("maintain", cwd=self.store, walk=True, env={"CTX_ACTOR": "tester"}), 5,
                   "STORE_NOT_NAMED: a write needs CTX_STORE or --store")
        self.fails(self.run_ctx("maintain", "now"), 1, "USAGE now: bad command line")


class SectionlessArchive(UpkeepCase):
    """The archive doc's own type decides where and in which order `maintain`
    moves entries: `## <name>` when `log.section` is declared, else the
    body-level dated list; newest-first at the top, oldest-first at the end."""

    def test_new_archive_with_no_section_is_sectionless_oldest_first(self):
        self.schema("log", {"log": {}})
        entries = self.big_log()
        self.assertEqual(self.run_ctx("maintain")[1],
                         "archived: 20 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
        archive = self.text("archive/sample-rollout-log")
        self.assertTrue(archive.startswith(
            "---\ntitle: Log of epics/sample-rollout\ntype: log\nupdated: 2026-01-08\n---\n\n"
            "# Log of epics/sample-rollout\n"))
        self.assertNotIn("##", archive)
        self.assertEqual([l for l in archive.split("\n") if l.startswith("- ")], entries[:20])
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))

    def test_new_archive_with_no_section_is_sectionless_newest_first(self):
        self.schema("log", {"log": {"order": "newest-first"}})
        entries = self.big_log()
        self.assertEqual(self.run_ctx("maintain")[0], 0)
        archive = self.text("archive/sample-rollout-log")
        self.assertNotIn("##", archive)
        self.assertEqual([l for l in archive.split("\n") if l.startswith("- ")], list(reversed(entries[:20])))
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))

    def test_a_newest_first_archive_type_can_still_use_a_section(self):
        self.schema("log", {"log": {"section": "Log", "order": "newest-first"}})
        entries = self.big_log()
        self.assertEqual(self.run_ctx("maintain")[0], 0)
        archive = self.text("archive/sample-rollout-log")
        self.assertIn("## Log\n", archive)
        self.assertEqual([l for l in archive.split("\n") if l.startswith("- ")], list(reversed(entries[:20])))
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))

    def test_a_second_sectionless_tail_lands_by_the_archives_order(self):
        for order in (None, "newest-first"):
            with self.subTest(order=order):
                if os.path.exists(self.path("archive/sample-rollout-log")):
                    os.unlink(self.path("archive/sample-rollout-log"))
                self.schema("log", {"log": {"order": order}} if order else {"log": {}})
                self.big_log()
                self.assertEqual(self.run_ctx("maintain")[0], 0)
                for day in range(41, 60):
                    self.assertEqual(self.run_ctx(
                        "log", EPIC, f"entry {day} " + "y" * 900, "--date", f"2026-02-{day - 40:02}")[0], 0)
                self.assertEqual(self.run_ctx("maintain")[1],
                                 "archived: 19 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
                archive = self.text("archive/sample-rollout-log")
                self.assertNotIn("##", archive)
                archived = [l for l in archive.split("\n") if l.startswith("- ")]
                self.assertEqual(len(archived), 39)
                self.assertEqual(archived, sorted(archived, reverse=order == "newest-first"))
                self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)

    def test_an_existing_archives_own_type_governs_it_not_the_default(self):
        self.schema("weekly", {"log": {}})
        self.put("archive/sample-rollout-log",
                  "---\ntitle: Log of epics/sample-rollout\ntype: weekly\nupdated: 2026-01-01\n---\n\n"
                  "# Log of epics/sample-rollout\n\n- 2026-01-01 — prior entry.\n")
        self.big_log()
        self.assertEqual(self.run_ctx("maintain")[0], 0)
        archive = self.text("archive/sample-rollout-log")
        self.assertIn("type: weekly\n", archive)
        self.assertNotIn("##", archive)
        self.assertIn("- 2026-01-01 — prior entry.\n", archive)
        self.assertEqual(self.run_ctx("validate")[0], 0)


class ArchiveDocValidates(UpkeepCase):
    """A freshly created archive doc carries a reasonable default for every
    field its type requires; a field with none is refused and nothing is
    written to either doc (#74)."""

    def test_required_fields_get_reasonable_defaults(self):
        self.schema("log", {"frontmatter": {
            "domain": {"required": True}, "status": {"required": True, "enum": ["open", "closed"]},
            "logged_on": {"required": True, "kind": "date"}, "updated": {"kind": "date"},
        }, "log": {}})
        self.put(EPIC, self.text(EPIC).replace("status: active\n", "status: active\ndomain: payments\n"))
        self.big_log()
        self.assertEqual(self.run_ctx("maintain")[1],
                         "archived: 20 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
        archive = self.text("archive/sample-rollout-log")
        self.assertIn("domain: payments\n", archive)
        self.assertIn("status: open\n", archive)
        self.assertIn("logged_on: 2026-01-08\n", archive)
        self.assertEqual(self.run_ctx("validate")[0], 0)
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))

    def test_a_field_with_no_derivable_default_is_refused(self):
        self.schema("log", {"frontmatter": {"owner": {"required": True}}, "log": {}})
        self.big_log()
        before = self.text(EPIC)
        self.assertEqual(self.run_ctx("maintain"),
                         (0, "archive: archive/sample-rollout-log — cannot create, owner required\n", ""))
        self.assertFalse(os.path.exists(self.path("archive/sample-rollout-log")))
        self.assertEqual(self.text(EPIC), before)
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_a_source_domain_outside_the_enum_falls_back(self):
        self.schema("log", {"frontmatter": {"domain": {"required": True, "enum": ["archive", "payments"]}},
                            "log": {}})
        self.put(EPIC, self.text(EPIC).replace("status: active\n", "status: active\ndomain: billing\n"))
        self.big_log()
        self.assertEqual(self.run_ctx("maintain")[1],
                         "archived: 20 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
        self.assertIn("domain: archive\n", self.text("archive/sample-rollout-log"))
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_a_const_field_takes_its_value(self):
        self.schema("log", {"frontmatter": {"priority": {"required": True, "const": "normal"}}, "log": {}})
        self.big_log()
        self.assertEqual(self.run_ctx("maintain")[1],
                         "archived: 20 log entries of epics/sample-rollout → archive/sample-rollout-log\n")
        self.assertIn("priority: normal\n", self.text("archive/sample-rollout-log"))
        self.assertEqual(self.run_ctx("validate")[0], 0)


class Git(UpkeepCase):
    def git(self, *args):
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        done = subprocess.run(["git", "-C", self.store, *args], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def setUp(self):
        super().setUp()
        if shutil.which("git") is None:
            self.skipTest("no git")
        self.git("init", "-q", "-b", "main")
        # no clean-up in the background: it would still be writing when the test removes the store
        self.git("config", "gc.auto", "0")
        self.git("config", "maintenance.auto", "false")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "start")
        self.env.update(CTX_GIT="1", PATH=os.environ["PATH"], GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def test_never_a_commit_per_write(self):
        self.assertEqual(self.run_ctx("log", EPIC, "one")[0], 0)
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "1")

    def test_maintain_commits_once_per_window(self):
        from tests.harness import ctx
        self.assertEqual(self.run_ctx("log", EPIC, "one")[0], 0)
        code, out, err = self.run_ctx("maintain")
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^committed: [0-9a-f]{7,}\n$")
        self.assertEqual(self.git("log", "-1", "--format=%an|%cn|%s").strip(), "tester|tester|ctx: maintain")
        self.assertEqual(self.git("status", "--porcelain").strip(), "")
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
        self.assertEqual(self.run_ctx("log", EPIC, "two")[0], 0)
        self.assertEqual(ctx("--now", "2026-01-08T09:33:00Z", "maintain", env=self.env), (0, "ok: nothing to do\n", ""))
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "2")
        self.assertRegex(ctx("--now", "2026-01-08T09:36:00Z", "maintain", env=self.env)[1], r"^committed: ")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "3")

    def test_a_git_failure_is_one_error_line(self):
        self.assertEqual(self.run_ctx("log", EPIC, "one")[0], 0)
        hook = os.path.join(self.store, ".git", "index.lock")
        with open(hook, "w") as handle:
            handle.write("held")
        self.fails(self.run_ctx("maintain"), 5, "GIT_FAILED add: version control refused the commit")
        os.unlink(hook)
        self.assertRegex(self.run_ctx("maintain")[1], r"^committed: ")

    def test_no_git_program(self):
        self.assertEqual(self.run_ctx("log", EPIC, "one")[0], 0)
        self.assertEqual(self.run_ctx("maintain", PATH="/nonexistent"), (0, "ok: nothing to do\n", ""))

    def test_off_by_default_and_outside_a_work_tree(self):
        self.assertEqual(self.run_ctx("log", EPIC, "one")[0], 0)
        self.assertEqual(self.run_ctx("maintain", CTX_GIT="")[1], "ok: nothing to do\n")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), "1")
        shutil.rmtree(os.path.join(self.store, ".git"))
        self.assertEqual(self.run_ctx("maintain"), (0, "ok: nothing to do\n", ""))
