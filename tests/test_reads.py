"""P2: the read verbs, the memory-tool verbs, the handler and the MCP adapter."""
import json
import os
import shutil

from tests.harness import FIXTURE, ctx, golden
from tests.test_store import EPIC, NOW, StoreCase

LINKS = (
    "---\ntitle: Links\ntype: reference\n---\n\n# Links\n\n"
    "See [locks](lock-modes.md#details), [[lock-modes]], [[reference/lock-modes|the doc]],\n"
    "[the epic](../epics/sample-rollout.md), [out](https://example.com/lock-modes.md) and [[other]].\n"
)


class Get(StoreCase):
    def test_section_tail_and_body(self):
        self.assertEqual(
            self.run_ctx("get", EPIC, "--section", "Session log", "--tail", "2"),
            (0, "- 2026-01-06 — region one done.\n- 2026-01-07 — region two done.\n", ""))
        self.assertEqual(self.run_ctx("get", EPIC, "--section", "Goal")[1],
                         "Roll the sample service out to every region.\n")
        code, out, _ = self.run_ctx("get", EPIC)
        self.assertEqual(out, golden("get-body.txt", out))

    def test_failures(self):
        self.fails(self.run_ctx("get", EPIC, "--section", "goal"), 2, "NO_SUCH_SECTION goal: no such section")
        self.fails(self.run_ctx("get", "epics/none"), 2, "NO_SUCH_DOC epics/none: no such doc")
        self.fails(self.run_ctx("get", "../x"), 3, "PATH_ESCAPE ../x: path leaves the store")
        self.fails(self.run_ctx("get", EPIC, "--tail", "0"), 1, "USAGE --tail: bad command line")
        self.fails(self.run_ctx("get", EPIC, "--out", "file"), 1, "USAGE --out: bad command line")
        self.fails(self.run_ctx("get", EPIC, "--out", "auto"), 1, "USAGE CTX_SCRATCH: bad command line")

    def test_over_8_kb_needs_full(self):
        self.put("reference/big", "---\ntitle: Big\ntype: reference\n---\n\n" + "a line of text\n" * 1000)
        capped = self.run_ctx("get", "reference/big", "--budget", "50000")[1]
        self.assertLessEqual(len(capped.encode()), 8192)
        self.assertRegex(capped, r"… \d+ more lines, raise --budget\n$")
        self.assertEqual(len(self.run_ctx("get", "reference/big", "--budget", "50000", "--full")[1]), 15000)

    def test_out_auto(self):
        scratch = os.path.join(self.work.name, "scratch")
        self.put("reference/big", "---\ntitle: Big\ntype: reference\n---\n\n" + "a line of text\n" * 1000)
        code, out, _ = self.run_ctx("get", "reference/big", "--out", "auto", CTX_SCRATCH=scratch)
        self.assertEqual((code, out), (0, os.path.join(scratch, "reference--big.md") + "\n"))
        with open(out.strip()) as handle:
            self.assertEqual(handle.read(), "a line of text\n" * 1000)


class Find(StoreCase):
    def test_golden(self):
        for name, args in (("find-region.txt", ("region",)), ("find-type.txt", ("--type", "session")),
                           ("find-tag.txt", ("--tag", "locks")), ("find-none.txt", ("zebra",))):
            code, out, err = self.run_ctx("find", *args)
            self.assertEqual((code, err), (0, ""), name)
            self.assertEqual(out, golden(name, out))

    def test_rows(self):
        self.put("reference/long", "---\ntitle: Long\ntype: reference\n---\n\n# H\n\n" + "word " * 40 + "needle\n")
        out = self.run_ctx("find", "LONG")[1].split("\n")
        self.assertEqual(out[0], "1 hits")
        key, title, updated, summary = out[1].split(" · ")
        self.assertEqual((key, title, updated), ("reference/long", "Long", "-"))
        self.assertEqual(len(summary), 80)
        self.assertTrue(summary.endswith("…"))

    def test_folds_behind_a_count(self):
        for number in range(60):
            self.put(f"reference/n{number:02}", f"---\ntitle: Note {number}\ntype: reference\n---\n\nneedle {number}\n")
        code, out, _ = self.run_ctx("find", "needle", "--budget", "600", "--json")
        data = json.loads(out)["data"]
        self.assertEqual((data["hits"], data["truncated"]), (60, True))
        self.assertLessEqual(data["bytes"], 600)
        shown = data["text"].split("\n")
        self.assertEqual(shown[-1], f"… {61 - (len(shown) - 1)} more hits, raise --budget")

    def test_every_term_has_to_occur(self):
        self.put("reference/apart", "---\ntitle: Apart\ntype: reference\n---\n\n## One\nThe dag failed.\n\n## Two\nA timeout followed.\n")
        self.put("reference/partly", "---\ntitle: Partly\ntype: reference\n---\n\nThe dag failed, nothing else.\n")
        for query in ("dag failed timeout", "timeout dag", "TIMEOUT  Failed"):
            self.assertEqual(self.run_ctx("find", query)[1].split("\n")[:2],
                             ["1 hits", "reference/apart · Apart · - · The dag failed. · § One"], query)
        self.assertEqual(self.run_ctx("find", "dag failed")[1].split("\n")[0], "2 hits")
        self.assertEqual(self.run_ctx("find", "dag failed zebra")[1], "0 hits\n")

    def test_a_phrase_in_quotes(self):
        self.put("reference/a", "---\ntitle: A\ntype: reference\n---\n\nThe dag failed twice.\n")
        self.put("reference/b", "---\ntitle: B\ntype: reference\n---\n\nIt failed, the dag did.\n")
        self.assertEqual(self.run_ctx("find", "dag failed")[1].split("\n")[:2],
                         ["2 hits", "reference/a · A · - · The dag failed twice."])
        self.assertEqual(self.run_ctx("find", '"dag failed"')[1], "1 hits\nreference/a · A · - · The dag failed twice.\n")
        self.assertEqual(self.run_ctx("find", '"dag failed" twice')[1].split("\n")[0], "1 hits")

    def test_plurals_hyphens_and_underscores(self):
        self.put("reference/v", "---\ntitle: Variants\ntype: reference\n---\n\n## Order model\nThe roll-out of load_orders.\n")
        for query in ("orders", "order", "rollout", "roll-out", "roll_out", "loadorders", "load-orders", "models"):
            self.assertIn("reference/v", self.run_ctx("find", query)[1], query)
        self.assertNotIn("reference/v", self.run_ctx("find", "rollout zebra")[1])

    def test_rank_and_section(self):
        self.put("reference/heading", "---\ntitle: H\ntype: reference\n---\n\n## Quokka care\nFeeding.\n\n## Other\nNothing.\n")
        self.put("reference/body", "---\ntitle: B\ntype: reference\n---\n\n## Notes\nA quokka was seen.\n")
        self.put("reference/quokka", "---\ntitle: Animals\ntype: reference\n---\n\nNo section, one quokka.\n")
        self.put("reference/titled", "---\ntitle: The quokka file\ntype: reference\n---\n\nText.\n")
        code, out, _ = self.run_ctx("find", "quokka", "--json")
        data = json.loads(out)["data"]
        self.assertEqual([row["doc"] for row in data["rows"]],
                         ["reference/quokka", "reference/titled", "reference/heading", "reference/body"])
        self.assertEqual([row["section"] for row in data["rows"]], [None, None, "Quokka care", "Notes"])
        self.assertEqual(data["text"].split("\n")[3], "reference/heading · H · - · Feeding. · § Quokka care")
        scores = [row["score"] for row in data["rows"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_a_long_query_is_a_task(self):
        self.put("reference/all", "---\ntitle: All\ntype: reference\n---\n\n## Notes\nThe quokka dag failed with a timeout on the warehouse.\n")
        self.put("reference/most", "---\ntitle: Most\ntype: reference\n---\n\n## Runbook\nWhen the quokka dag failed with a timeout, rerun it.\n")
        self.put("reference/one", "---\ntitle: One\ntype: reference\n---\n\nOnly a warehouse here.\n")
        prompt = "Can you check why the quokka dag failed last night, was it a timeout on the warehouse?"
        code, out, _ = self.run_ctx("find", prompt, "--json")
        data = json.loads(out)["data"]
        self.assertEqual([(row["doc"], row["section"]) for row in data["rows"]],
                         [("reference/all", "Notes"), ("reference/most", "Runbook")])
        self.assertGreater(data["rows"][0]["terms"], data["rows"][1]["terms"])
        self.assertEqual(data["rows"][0]["of"], 15)  # 17 words: "the" twice, and "a" is no term
        lines = data["text"].split("\n")
        self.assertEqual(lines[0], "2 hits")
        self.assertRegex(lines[1], r"^reference/all · All · - · .* · § Notes · \d+ of 15 terms$")
        self.assertRegex(lines[2], r"^reference/most · Most · - · .* · § Runbook · \d+ of 15 terms$")

    def test_one_rare_term_can_be_most_of_a_task(self):
        for number in range(30):
            self.put(f"reference/c{number:02}", f"---\ntitle: C{number}\ntype: reference\n---\n\ncheck the status of the job\n")
        self.put("reference/rare", "---\ntitle: R\ntype: reference\n---\n\nAbout load_quokka_v2 only.\n")
        out = self.run_ctx("find", "check status job load_quokka_v2")[1].split("\n")
        self.assertEqual(out[0], "1 hits")
        self.assertEqual(out[1], "reference/rare · R · - · About load_quokka_v2 only. · 1 of 4 terms")
        # a word that no doc holds weighs nothing: the docs that hold the rest are the hits
        out = self.run_ctx("find", "check status job zebra_v9")[1].split("\n")
        self.assertEqual(out[0], "30 hits")
        self.assertTrue(out[1].endswith(" · 3 of 4 terms"))

    def test_a_short_query_needs_every_term(self):
        self.put("reference/most", "---\ntitle: Most\ntype: reference\n---\n\nThe quokka dag failed.\n")
        self.assertEqual(self.run_ctx("find", "quokka dag timeout")[1], "0 hits\n")
        self.assertEqual(self.run_ctx("find", "quokka dag failed")[1].split("\n")[0], "1 hits")
        self.assertNotIn(" terms", self.run_ctx("find", "quokka dag failed")[1])

    def test_a_doc_that_holds_every_term_comes_first(self):
        self.put("reference/a-most", "---\ntitle: Quokka dag\ntype: reference\n---\n\n## Quokka dag\nquokka dag failed, quokka dag failed.\n")
        self.put("reference/z-all", "---\ntitle: Z\ntype: reference\n---\n\nquokka dag failed after a timeout\n")
        out = self.run_ctx("find", "quokka dag failed timeout")[1].split("\n")
        self.assertEqual([line.split(" · ")[0] for line in out[1:3]], ["reference/z-all", "reference/a-most"])

    def test_punctuation_around_words(self):
        self.put("reference/p", "---\ntitle: P\ntype: reference\n---\n\nYesterday an outage; the quokka glitch.\n")
        for query in ("quokka,", "(quokka)", "glitch?", "yesterday's", "outage; quokka!", "'quokka'"):
            self.assertEqual(self.run_ctx("find", "--", query)[1].split("\n")[0], "1 hits", query)
        self.fails(self.run_ctx("find", "--", "? , a"), 1, "USAGE query: bad command line")

    def test_a_rare_term_counts_for_more(self):
        for number in range(12):
            self.put(f"reference/c{number:02}", f"---\ntitle: Common {number}\ntype: reference\n---\n\ncommon word\n")
        self.put("reference/by-common", "---\ntitle: About the common thing\ntype: reference\n---\n\nand a quokka\n")
        self.put("reference/by-rare", "---\ntitle: About the quokka\ntype: reference\n---\n\nand a common thing\n")
        data = json.loads(self.run_ctx("find", "common quokka", "--json")[1])["data"]
        self.assertEqual([row["doc"] for row in data["rows"]], ["reference/by-rare", "reference/by-common"])
        self.assertGreater(data["rows"][0]["score"], data["rows"][1]["score"])

    def test_a_phrase_among_other_terms_is_rewarded(self):
        self.put("reference/apart", "---\ntitle: A\ntype: reference\n---\n\nfailed dag, then twice the dag failed again\n")
        self.put("reference/phrase", "---\ntitle: B\ntype: reference\n---\n\nthe dag failed twice\n")
        data = json.loads(self.run_ctx("find", '"dag failed" twice', "--json")[1])["data"]
        self.assertEqual([row["doc"] for row in data["rows"]], ["reference/phrase", "reference/apart"])

    def test_more_hits_than_are_scored(self):
        for number in range(340):
            self.put(f"reference/n{number:03}", f"---\ntitle: Note {number}\ntype: reference\n---\n\nneedle {number}\n")
        self.put("zz/needle-in-the-key", "---\ntitle: Last by key\ntype: reference\n---\n\ntext\n")
        out = self.run_ctx("find", "needle")[1].split("\n")
        self.assertEqual(out[:2], ["341 hits", "zz/needle-in-the-key · Last by key · - · text"])
        scratch = os.path.join(self.work.name, "scratch")
        path = self.run_ctx("find", "needle", "--out", "auto", CTX_SCRATCH=scratch)[1].strip()
        with open(path) as handle:
            rows = handle.read().splitlines()
        self.assertEqual(len(rows), 342)
        self.assertEqual(rows[-1], "reference/n339 · Note 339 · - · needle 339")

    def test_case_and_non_ascii(self):
        self.put("reference/umlaut", "---\ntitle: Über Größe\ntype: reference\n---\n\nDie STRASSE ist lang.\n")
        for query in ("über", "ÜBER", "größe", "strasse", "Strasse", "größe strasse", "lang über"):
            self.assertEqual(self.run_ctx("find", query)[1].split("\n")[0], "1 hits", query)
        self.assertTrue(self.run_ctx("find", "GRÖSSE")[1].startswith("0 hits"))

    def test_a_doc_that_does_not_parse_is_not_a_hit(self):
        self.put("reference/broken", "no frontmatter but the word region\n")
        with open(self.path("reference/binary"), "wb") as handle:
            handle.write(b"---\ntitle: B\ntype: reference\n---\n\nregion \xff\xfe r\xc3\xa9gion\n")
        self.assertEqual(self.run_ctx("find", "region")[1].split("\n")[0], "2 hits")
        self.assertEqual(self.run_ctx("find", "région")[1].split("\n")[0], "0 hits")

    def test_type_and_tag_with_frontmatter_longer_than_the_head(self):
        self.put("reference/long", "---\ntitle: " + "x" * 6000 + "\ntype: reference\ntags: [locks, long]\n---\n\nbody\n")
        self.assertEqual(self.run_ctx("find", "--tag", "long")[1].split("\n")[0], "1 hits")
        self.assertEqual(self.run_ctx("find", "--type", "reference")[1].split("\n")[0], "2 hits")

    def test_hits_past_the_budget_are_counted(self):
        for number in range(80):
            self.put(f"reference/n{number:02}", f"---\ntitle: Note {number}\ntype: reference\n---\n\nneedle {number}\n")
        self.put("reference/n99", "needle, and no frontmatter\n")
        data = json.loads(self.run_ctx("find", "needle", "--budget", "500", "--json")[1])["data"]
        self.assertEqual((data["hits"], data["truncated"]), (80, True))
        scratch = os.path.join(self.work.name, "scratch")
        path = self.run_ctx("find", "needle", "--out", "auto", CTX_SCRATCH=scratch)[1].strip()
        with open(path) as handle:
            self.assertEqual(len(handle.read().splitlines()), 81)

    def test_usage(self):
        self.fails(self.run_ctx("find"), 1, "USAGE query: bad command line")


class Resolve(StoreCase):
    def test_field_wins_over_section(self):
        self.assertEqual(self.run_ctx("resolve", "EX-2")[1],
                         "sessions/beta-docs · beta-docs · 2026-01-07T10:00:00Z · Owns the EX-2 lane.\n")
        os.unlink(self.path("sessions/alpha-rollout"))
        self.assertEqual(self.run_ctx("resolve", "EX-1", "--json")[1:], self.run_ctx("resolve", "EX-1", "--json")[1:])
        self.assertEqual(json.loads(self.run_ctx("resolve", "EX-1", "--json")[1])["data"]["doc"], EPIC)

    def test_a_doc_that_does_not_parse_does_not_stop_resolve(self):
        self.put("reference/broken", "EX-2 but no frontmatter\n")
        self.assertEqual(self.run_ctx("resolve", "EX-2")[0], 0)

    def test_failures(self):
        self.fails(self.run_ctx("resolve", "EX-99"), 2, "NO_SUCH_DOC EX-99: no such doc")
        self.fails(self.run_ctx("resolve", "EX-1x"), 2, "NO_SUCH_DOC EX-1x: no such doc")
        self.fails(self.run_ctx("resolve", "ex-1"), 2, "NO_SUCH_DOC ex-1: no such doc")
        self.put("sessions/twin", self.text("sessions/alpha-rollout").replace("alpha-rollout", "twin").replace("sid-alpha", "sid-twin"))
        self.fails(self.run_ctx("resolve", "EX-1"), 3,
                   "AMBIGUOUS_SELECTOR EX-1 (sessions/alpha-rollout, sessions/twin): selector matches more than one target")
        self.fails(self.run_ctx("resolve"), 1, "USAGE key: bad command line")

    def test_key_shape_is_the_stores(self):
        with open(os.path.join(self.store, "ctx-store.json"), "w") as handle:
            handle.write('{"schema_version": 1}')
        self.fails(self.run_ctx("resolve", "EX-1"), 1, "USAGE resolve.key_regex: bad command line")
        with open(os.path.join(self.store, "ctx-store.json"), "w") as handle:
            json.dump({"schema_version": 1, "resolve": {"key_regex": "#[0-9]+", "section": "Tracker & links"}}, handle)
        self.put(EPIC, self.text(EPIC).replace("EX-1", "#12 and #123"))
        self.assertEqual(self.run_ctx("resolve", "#12")[0], 0)
        self.fails(self.run_ctx("resolve", "#1"), 2, "NO_SUCH_DOC #1: no such doc")


class Federation(StoreCase):
    def setUp(self):
        super().setUp()
        self.second = os.path.join(self.work.name, "second")
        shutil.copytree(FIXTURE, self.second)
        shutil.rmtree(os.path.join(self.second, "sessions"))
        with open(os.path.join(self.second, "reference", "only-here.md"), "w") as handle:
            handle.write("---\ntitle: Only here\ntype: reference\n---\n\n## Log\n\nsecond store\n")
        self.env["CTX_STORE"] = os.pathsep.join([self.store, self.second])

    def test_reads_cover_the_list(self):
        self.assertEqual(self.run_ctx("get", "reference/only-here", "--section", "Log")[1], "second store\n")
        out = self.run_ctx("find", "--type", "reference")[1]
        self.assertEqual(out, "2 hits\n1:reference/lock-modes · Lock modes · 2026-01-05 · `flock` on local filesystems, "
                              "the `mkdir` lock where flock cannot be proven.\n"
                              "2:reference/only-here · Only here · - · second store\n")

    def test_a_write_goes_to_the_store_that_holds_the_doc(self):
        self.assertEqual(self.run_ctx("log", "reference/only-here", "--section", "Log", "x")[0], 0)
        self.assertTrue(os.path.exists(os.path.join(self.second, ".audit", "tester.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.store, ".audit")))
        self.assertEqual(self.run_ctx("new", "reference", "reference/fresh")[0], 0)
        self.assertTrue(os.path.exists(self.path("reference/fresh")))


class MemoryVerbs(StoreCase):
    def test_view(self):
        code, out, _ = self.run_ctx("view", EPIC)
        self.assertEqual(out, golden("view-doc.txt", out))
        self.assertEqual(self.run_ctx("view", EPIC, "--range", "2:3")[1], "2  title: Sample rollout\n3  type: epic\n")
        out = self.run_ctx("view")[1]
        self.assertEqual(out, golden("view-store.txt", out))
        self.assertEqual(self.run_ctx("view", "sessions")[1].split("\n")[0], "3 docs")
        self.fails(self.run_ctx("view", "nothing"), 2, "NO_SUCH_DOC nothing: no such doc")
        self.fails(self.run_ctx("view", ".audit"), 3, "PATH_ESCAPE .audit: path leaves the store")
        self.fails(self.run_ctx("view", EPIC, "--range", "3:2"), 1, "USAGE --range: bad command line")

    def test_new_from_template(self):
        self.assertEqual(self.run_ctx("new", "epic", "epics/next", "--title", "Next thing"),
                         (0, "created: epics/next\n", ""))
        self.assertEqual(self.text("epics/next"), golden("new-epic.md", self.text("epics/next")))
        self.fails(self.run_ctx("new", "epic", "epics/next"), 3, "DOC_EXISTS epics/next: doc exists")
        self.assertEqual(self.run_ctx("new", "reference", "reference/plain")[0], 0)
        self.assertEqual(self.text("reference/plain"),
                         "---\ntitle: plain\ntype: reference\nupdated: 2026-01-08\n---\n\n# plain\n")
        self.assertEqual(self.run_ctx("validate")[0], 0)

    def test_create(self):
        text = "---\ntitle: A\ntype: reference\n---\n\nhello"
        self.assertEqual(self.run_ctx("create", "--stdin", stdin=json.dumps({"doc": "notes/a", "text": text}))[0], 0)
        self.assertEqual(self.text("notes/a"), text + "\n")
        self.assertEqual(self.run_ctx("create", "notes/a", "--type", "epic", "--title", "Now an epic")[0], 0)
        rows = self.audit()
        self.assertEqual([row["verb"] for row in rows], ["create", "create"])
        self.assertEqual(rows[1]["before"], rows[0]["after"])
        self.fails(self.run_ctx("create", "notes/b", "no frontmatter"), 3, "SCHEMA_VIOLATION frontmatter: schema violation")
        self.fails(self.run_ctx("create", "notes/b"), 1, "USAGE text: bad command line")
        self.assertFalse(os.path.exists(self.path("notes/b")))

    def test_str_replace(self):
        self.assertEqual(self.run_ctx("str_replace", EPIC, "--old", "Region three.", "--new", "Regions three and four.")[0], 0)
        self.assertIn("- Regions three and four.\n", self.text(EPIC))
        before = self.text(EPIC)
        self.fails(self.run_ctx("str_replace", EPIC, "--old", "absent"), 2, "NO_MATCH epics/sample-rollout: text not found in the doc")
        self.fails(self.run_ctx("str_replace", EPIC, "--old", "region"), 3,
                   "AMBIGUOUS_SELECTOR epics/sample-rollout (3 matches): selector matches more than one target")
        self.fails(self.run_ctx("str_replace", EPIC, "--old", "status: active", "--new", "status: asleep"), 3,
                   "SCHEMA_VIOLATION status: schema violation")
        self.fails(self.run_ctx("str_replace", EPIC, "--old", "Regions", "--new", "ghp_" + "a" * 36), 3,
                   "SECRET_DETECTED github-token: payload looks like a secret")
        self.assertEqual(self.text(EPIC), before)
        self.assertEqual(self.run_ctx("str_replace", EPIC, "--old", "- Regions three and four.\n")[0], 0)

    def test_insert(self):
        self.assertEqual(self.run_ctx("insert", EPIC, "--line", "0", "x")[0], 3)
        lines = self.text(EPIC).split("\n")
        where = lines.index("## Goal") + 1
        self.assertEqual(self.run_ctx("insert", EPIC, "--line", str(where), "first\nsecond")[0], 0)
        self.assertEqual(self.text(EPIC).split("\n")[where - 1:where + 3],
                         ["## Goal", "first", "second", "Roll the sample service out to every region."])
        total = len(self.text(EPIC).split("\n")) - 1
        self.assertEqual(self.run_ctx("insert", EPIC, "--line", str(total), "- 2026-01-08 — last.")[0], 0)
        self.assertTrue(self.text(EPIC).endswith("- 2026-01-08 — last.\n"))
        self.fails(self.run_ctx("insert", EPIC, "--line", str(total + 2), "x"), 1, "USAGE --line: bad command line")

    def test_delete(self):
        self.put("reference/links", LINKS)
        code, out, _ = self.run_ctx("delete", "reference/lock-modes")
        self.assertEqual((code, out), (0, "deleted: reference/lock-modes (1 docs still link to it: reference/links)\n"))
        self.assertFalse(os.path.exists(self.path("reference/lock-modes")))
        self.assertEqual(self.audit()[0]["after"], None)
        self.fails(self.run_ctx("delete", "reference/lock-modes"), 2, "NO_SUCH_DOC reference/lock-modes: no such doc")
        self.fails(self.run_ctx("delete", "sessions/alpha-rollout"), 3,
                   "NOT_OWNER sessions/alpha-rollout: doc is owned by another actor")
        self.fails(self.run_ctx("delete", "INDEX"), 3, "GENERATED INDEX: doc is generated")

    def test_rename_rewrites_links(self):
        self.put("reference/links", LINKS)
        self.put("reference/lock-modes", self.text("reference/lock-modes") + "\nBack to [links](links.md) and [[links]].\n")
        code, out, _ = self.run_ctx("rename", "reference/lock-modes", "archive/old/locks")
        self.assertEqual((code, out), (0, "renamed: reference/lock-modes → archive/old/locks (1 docs relinked)\n"))
        self.assertEqual(self.text("reference/links"), (
            "---\ntitle: Links\ntype: reference\n---\n\n# Links\n\n"
            "See [locks](../archive/old/locks.md#details), [[locks]], [[archive/old/locks|the doc]],\n"
            "[the epic](../epics/sample-rollout.md), [out](https://example.com/lock-modes.md) and [[other]].\n"))
        self.assertTrue(self.text("archive/old/locks").endswith("\nBack to [links](../../reference/links.md) and [[links]].\n"))
        self.assertFalse(os.path.exists(self.path("reference/lock-modes")))
        rows = [(row["verb"], row["doc"], row["after"] is None) for row in self.audit()]
        self.assertEqual(rows, [("rename", "archive/old/locks", False), ("rename", "reference/lock-modes", True),
                                ("rename", "reference/links", False)])
        self.assertEqual(self.run_ctx("move", "archive/old/locks", "reference/lock-modes")[1],
                         "moved: archive/old/locks → reference/lock-modes (1 docs relinked)\n")
        self.assertEqual(self.text("reference/links"), LINKS.replace("[[reference/lock-modes|", "[[reference/lock-modes|"))

    def test_an_ignored_path_is_not_a_doc(self):
        self.put("README", "---\ntitle: R\ntype: reference\n---\n\nhello\n")
        before = self.text("README")
        for verb in (("view", "README"), ("get", "README"), ("str_replace", "README", "--old", "hello", "--new", "x"),
                     ("insert", "README", "--line", "0", "x"), ("delete", "README"), ("rename", "README", "notes/r"),
                     ("create", "README", "--type", "reference"), ("new", "reference", "README")):
            self.fails(self.run_ctx(*verb), 2, "NO_SUCH_DOC README: no such doc")
        self.fails(self.run_ctx("rename", EPIC, "README"), 2, "NO_SUCH_DOC README: no such doc")
        self.assertEqual(self.text("README"), before)
        self.assertEqual(self.audit(), [])

    def test_a_shared_name_is_not_a_wikilink_target(self):
        self.put("archive/lock-modes", "---\ntitle: Old\ntype: reference\n---\n\nold\n")
        self.put("reference/links", LINKS)
        self.assertEqual(self.run_ctx("rename", "reference/lock-modes", "reference/locks")[0], 0)
        text = self.text("reference/links")
        self.assertIn("[locks](locks.md#details), [[lock-modes]], [[reference/locks|the doc]]", text)

    def test_view_listing_budget(self):
        for number in range(250):
            self.put(f"reference/a-long-name-for-a-doc-{number:03}", "---\ntitle: N\ntype: reference\n---\n")
        out = self.run_ctx("view", "reference")[1]
        self.assertGreater(len(out.encode()), 4096)
        self.assertLessEqual(len(out.encode()), 8192)
        self.assertRegex(out, r"… \d+ more docs, raise --budget\n$")

    def test_rename_a_doc_that_is_not_utf8(self):
        with open(self.path("reference/binary"), "wb") as handle:
            handle.write(b"---\ntitle: B\ntype: reference\n---\n\n\xff\xfe\n")
        self.fails(self.run_ctx("rename", "reference/binary", "reference/b2"), 3, "SCHEMA_VIOLATION encoding: schema violation")
        self.assertTrue(os.path.exists(self.path("reference/binary")))

    def test_rename_failures(self):
        before = sorted(name for _, _, files in os.walk(self.store) for name in files if name != "store.lock")
        self.fails(self.run_ctx("rename", EPIC, "reference/lock-modes"), 3, "DOC_EXISTS reference/lock-modes: doc exists")
        self.fails(self.run_ctx("rename", "epics/none", "epics/other"), 2, "NO_SUCH_DOC epics/none: no such doc")
        self.fails(self.run_ctx("rename", EPIC, "../out"), 3, "PATH_ESCAPE ../out: path leaves the store")
        self.fails(self.run_ctx("rename", EPIC, "INDEX"), 3, "GENERATED INDEX: doc is generated")
        self.fails(self.run_ctx("rename", EPIC, EPIC), 1, "USAGE to: bad command line")
        self.assertEqual(sorted(name for _, _, files in os.walk(self.store) for name in files if name != "store.lock"), before)

    def test_a_session_of_verbs_leaves_no_unaudited_write(self):
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)
        self.put("reference/links", LINKS)
        self.assertEqual(self.run_ctx("validate", "--changed", "--adopt")[0], 0)
        for args in (("new", "epic", "epics/next"), ("log", "epics/next", "step one"), ("fm", "epics/next", "status", "paused"),
                     ("str_replace", "epics/next", "--old", "step one", "--new", "step 1"),
                     ("insert", "epics/next", "--line", "12", "A goal."), ("rename", "reference/lock-modes", "reference/locks"),
                     ("create", "notes/n", "--type", "reference"), ("delete", "notes/n"),
                     ("touch", "--session", "sid-alpha")):
            self.assertEqual(self.run_ctx(*args)[0], 0, args)
        self.assertEqual(self.run_ctx("validate", "--changed"), (0, "ok: 0 docs checked\n", ""))
        self.assertEqual(self.run_ctx("validate")[0], 0)


class MemoryTool(StoreCase):
    def call(self, **fields):
        return self.run_ctx("memory", stdin=json.dumps(fields))

    def test_the_six_commands(self):
        text = "---\ntitle: A\ntype: reference\n---\n\nhello world\n"
        self.assertEqual(self.call(command="create", path="/memories/notes/a.md", file_text=text),
                         (0, "created: notes/a\n", ""))
        self.assertEqual(self.call(command="str_replace", path="/memories/notes/a.md", old_str="world", new_str="store")[0], 0)
        self.assertEqual(self.call(command="insert", path="/memories/notes/a.md", insert_line=6, insert_text="more")[0], 0)
        self.assertEqual(self.call(command="view", path="/memories/notes/a.md", view_range=[6, 7])[1],
                         "6  hello store\n7  more\n")
        self.assertEqual(self.call(command="rename", old_path="/memories/notes/a.md", new_path="/memories/notes/b.md")[0], 0)
        self.assertEqual(self.call(command="view", path="/memories/notes")[1], "1 docs\nnotes/b.md (51 bytes)\n")
        self.assertEqual(self.call(command="view", path="/memories")[1].split("\n")[0], "7 docs")
        self.assertEqual(self.call(command="delete", path="/memories/notes/b.md")[0], 0)
        self.assertEqual([row["verb"] for row in self.audit()],
                         ["create", "str_replace", "insert", "rename", "rename", "delete"])

    def test_failures(self):
        self.fails(self.call(command="view", path="/etc/passwd"), 3, "PATH_ESCAPE /etc/passwd: path leaves the store")
        self.fails(self.call(command="view", path="/memories/../etc/passwd"), 3,
                   "PATH_ESCAPE ../etc/passwd: path leaves the store")
        self.fails(self.call(command="view", path="/memoriesx/a.md"), 3, "PATH_ESCAPE /memoriesx/a.md: path leaves the store")
        self.fails(self.call(command="chmod", path="/memories/a.md"), 1, "USAGE command: bad command line")
        self.fails(self.call(command="view", path="/memories/a.md", mode="raw"), 1, "USAGE mode: bad command line")
        self.fails(self.call(command="view", path="/memories/a.md", view_range="1:2"), 1, "USAGE view_range: bad command line")
        self.fails(self.call(command="str_replace", path="/memories/epics/sample-rollout.md", old_str="region", new_str="x"), 3,
                   "AMBIGUOUS_SELECTOR epics/sample-rollout (3 matches): selector matches more than one target")
        self.fails(self.call(command="create", path="/memories/notes/a.md"), 1, "USAGE text: bad command line")
        self.fails(self.run_ctx("memory", stdin="not json"), 1, "USAGE stdin: bad command line")


class Mcp(StoreCase):
    def talk(self, *messages, **env):
        lines = "".join(json.dumps(message) + "\n" if not isinstance(message, str) else message + "\n" for message in messages)
        code, out, err = ctx("mcp", env={**self.env, **env}, stdin=lines)
        self.assertEqual((code, err), (0, ""))
        return [json.loads(line) for line in out.splitlines()]

    def request(self, ident, method, **params):
        return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}

    def tool(self, ident, name, **arguments):
        return self.request(ident, "tools/call", name=name, arguments=arguments)

    def test_handshake_and_tools(self):
        hello, pong, listing = self.talk(
            self.request(1, "initialize", protocolVersion="2025-03-26", capabilities={}, clientInfo={"name": "t", "version": "1"}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.request("p", "ping"),
            self.request(2, "tools/list"))
        self.assertEqual(hello, {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2025-03-26", "capabilities": {"tools": {}},
            "serverInfo": {"name": "ctx", "version": "0.1.0"}}})
        self.assertEqual(pong, {"jsonrpc": "2.0", "id": "p", "result": {}})
        tools = {tool["name"]: tool for tool in listing["result"]["tools"]}
        self.assertEqual(sorted(tools), sorted(
            "ctx_" + verb for verb in ("view create str_replace insert delete rename log fm new move brief find "
                                       "resolve get validate touch doctor migrate maintain").split()))
        self.assertEqual(tools["ctx_log"]["inputSchema"], {
            "type": "object", "additionalProperties": False, "required": ["doc", "text"],
            "properties": {"doc": {"type": "string"}, "text": {"type": "string"}, "section": {"type": "string"},
                           "date": {"type": "string"}}})
        self.assertTrue(tools["ctx_log"]["description"].startswith("ctx log <doc> <text>"))
        self.assertEqual(self.talk(self.request(1, "initialize", protocolVersion="1999-01-01"))[0]["result"]["protocolVersion"],
                         "2025-06-18")

    def test_brief_find_and_log(self):
        brief, find, log, tail = self.talk(
            self.tool(1, "ctx_brief", registry=True),
            self.tool(2, "ctx_find", query="region"),
            self.tool(3, "ctx_log", doc=EPIC, text="from the desktop", date="2026-01-09"),
            self.tool(4, "ctx_get", doc=EPIC, section="Session log", tail=1))
        self.assertEqual(brief["result"], {"isError": False, "content": [{"type": "text", "text": (
            "sessions: 2 not ended\n- alpha-rollout · active · EX-1 · region three · 2026-01-07T10:00:00Z\n"
            "- beta-docs · idle · EX-2 · waiting for review · 2026-01-07T10:00:00Z")}]})
        self.assertTrue(find["result"]["content"][0]["text"].startswith("2 hits\n"))
        self.assertEqual(log["result"]["content"][0]["text"], "logged: epics/sample-rollout")
        self.assertEqual(tail["result"]["content"][0]["text"], "- 2026-01-09 — from the desktop")
        self.assertEqual([(row["verb"], row["actor"]) for row in self.audit()], [("log", "tester")])

    def test_doctor(self):
        reply, = self.talk(self.tool(1, "ctx_doctor"))
        self.assertFalse(reply["result"]["isError"])
        self.assertIn(f"store: {os.path.realpath(self.store)} (markdown, from env)", reply["result"]["content"][0]["text"])

    def test_failures_are_tool_results(self):
        replies = self.talk(
            self.tool(1, "ctx_log", doc="epics/none", text="x"),
            self.tool(2, "ctx_log", doc=EPIC, text="x", sektion="Goal"),
            self.tool(3, "ctx_log", doc=EPIC),
            self.tool(4, "ctx_get", doc=EPIC, out="auto"),
            self.tool(5, "ctx_log", doc=EPIC, text="ghp_" + "a" * 36))
        self.assertEqual([reply["result"]["content"][0]["text"] for reply in replies], [
            "NO_SUCH_DOC epics/none: no such doc", "USAGE sektion: bad command line", "USAGE text: bad command line",
            "USAGE out: bad command line", "SECRET_DETECTED github-token: payload looks like a secret"])
        self.assertTrue(all(reply["result"]["isError"] for reply in replies))
        self.assertEqual(self.audit(), [])

    def test_protocol_errors(self):
        replies = self.talk(
            "not json",
            self.request(1, "tools/call", name="rm", arguments={}),
            self.request(2, "resources/list"),
            {"id": 3, "method": "ping"},
            self.request(4, "tools/call", name="ctx_row", arguments={}))
        self.assertEqual([reply["error"]["code"] for reply in replies], [-32700, -32602, -32601, -32600, -32602])
        self.assertEqual([reply["id"] for reply in replies], [None, 1, 2, None, 4])

    def test_a_write_needs_a_named_store(self):
        code, out, _ = ctx("mcp", env={"CTX_ACTOR": "tester"}, cwd=self.store, walk=True,
                           stdin=json.dumps(self.tool(1, "ctx_log", doc=EPIC, text="x")) + "\n")
        self.assertEqual(json.loads(out)["result"]["content"][0]["text"],
                         "STORE_NOT_NAMED: a write needs CTX_STORE or --store")
