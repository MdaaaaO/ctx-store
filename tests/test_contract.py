import json
import unittest

from ctxstore import spec
from ctxstore.cli import VERBS
from ctxstore.contract import ERRORS
from tests.harness import ctx, golden


class Contract(unittest.TestCase):
    def test_version(self):
        self.assertEqual(ctx("--version"), (0, "ctx 0.1.0 (api 1)\n", ""))

    def test_version_json(self):
        code, out, _ = ctx("--version", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out),
            {"api": 1, "ok": True, "verb": "version", "data": {"version": "0.1.0"}},
        )

    def test_help_golden(self):
        code, out, err = ctx("help")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, golden("help.txt", out))
        self.assertEqual(ctx()[1], out)
        self.assertEqual(ctx("--help")[1], out)

    def test_help_doctor_golden(self):
        code, out, _ = ctx("help", "doctor")
        self.assertEqual(code, 0)
        self.assertEqual(out, golden("help-doctor.txt", out))

    def test_every_verb_is_specified(self):
        self.assertEqual(sorted(spec.verbs()), sorted(VERBS))
        for verb in VERBS:
            code, out, _ = ctx("help", verb)
            self.assertEqual(code, 0, verb)
            self.assertTrue(out.startswith(f"ctx {verb}\n\nctx {verb}"), verb)

    def test_topics(self):
        overview = ctx("help")[1]
        line = next(l for l in overview.splitlines() if l.startswith("Topics"))
        for topic in line.split()[1:]:
            self.assertEqual(ctx("help", topic)[0], 0, topic)

    def test_error_table_matches_spec(self):
        rows = {}
        for line in spec.topics()["errors"][1].splitlines():
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells[0].startswith("`"):
                rows[cells[0].strip("`")] = (int(cells[1]), cells[2])
        self.assertEqual(rows, ERRORS)

    def test_unbuilt_verb(self):
        code, out, err = ctx("log", "some-doc")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(
            err, "NOT_BUILT log: verb is specified but not built in this version\n"
        )

    def test_unknown_verb_and_option(self):
        self.assertEqual(ctx("frobnicate")[0], 1)
        self.assertEqual(ctx("doctor", "--colour")[2], "USAGE --colour: bad command line\n")
        self.assertEqual(ctx("doctor", "extra")[0], 1)
        self.assertEqual(ctx("help", "nothing")[0], 1)
        self.assertEqual(ctx("--store")[0], 1)

    def test_error_json(self):
        code, out, err = ctx("--json", "frobnicate")
        self.assertEqual(code, 1)
        self.assertEqual(err, "USAGE frobnicate: bad command line\n")
        self.assertEqual(
            json.loads(out),
            {
                "api": 1,
                "ok": False,
                "error": {
                    "code": "USAGE",
                    "detail": "frobnicate",
                    "message": "bad command line",
                    "exit": 1,
                },
            },
        )

    def test_option_value_is_not_an_option(self):
        code, out, err = ctx("--store", "--json", "doctor")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "NO_STORE --json: no store found\n")

    def test_no_colour(self):
        for args in (("help",), ("help", "errors"), ("--version",)):
            self.assertNotIn("\x1b", ctx(*args)[1])


if __name__ == "__main__":
    unittest.main()
