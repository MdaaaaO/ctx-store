#!/usr/bin/env python3
"""Which PRs auto-merge without a reviewer verdict: the shape check of .github/workflows/auto-merge.yml.

    pr-shape.py <owner> <pr.json> <files.jsonl>

<pr.json> is `gh api repos/O/R/pulls/N`; <files.jsonl> is `gh api --paginate repos/O/R/pulls/N/files --jq '.[]'`
(one file object per line). Prints one word and exits 0:
    release     a release PR by the owner: `chore(release): X.Y.Z`, exactly CHANGELOG.md and ctxstore/VERSION
    dependabot  a bump by dependabot[bot]: `ci(deps): …`, only .github/workflows/*.yml, every changed line a
                `uses: owner/repo@<40-hex sha>` pin (a `# vN` comment may follow)
    review      anything else: the reviewer's verdict decides
Exits 1 with the reason on stderr when a PR looks like one of the two but is not of that shape: it waits
for the owner. The shape comes from the author, the file list and the diff, never from the title alone.
Tests: tests/test_pr_shape.py.
"""
import json
import re
import sys

RELEASE_FILES = ["CHANGELOG.md", "ctxstore/VERSION"]
WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
PIN = re.compile(r"^[+-] *(- )?uses: [A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}( +#.*)?$")


def shape(owner, pr, files):
    """(kind, reason): kind is release | dependabot | review; None with the reason when a PR of one of the
    two kinds is not of its shape."""
    author = pr["user"]["login"]
    title = pr["title"]
    names = [f["filename"] for f in files]
    if title.startswith("chore(release): "):
        if author != owner:
            return None, f"release PR is by {author}, not the owner"
        if sorted(names) != RELEASE_FILES:
            return None, "release PR changes more than CHANGELOG.md and ctxstore/VERSION"
        return "release", ""
    if author == "dependabot[bot]":
        if not title.startswith("ci(deps): "):
            return None, "dependabot PR is not a ci(deps) bump"
        other = [n for n in names if not WORKFLOW.match(n)]
        if other:
            return None, "dependabot PR changes " + " ".join(other) + ", not only workflows"
        for f in files:
            # GitHub omits the patch of a file whose diff is too large: that counts as a changed line too.
            lines = f["patch"].splitlines() if f.get("patch") else ["+(no patch)"]
            bad = [line for line in lines if line.startswith(("+", "-")) and not PIN.match(line)]
            if bad:
                return None, f"dependabot PR changes a line that is not a uses: pin in {f['filename']}: {bad[0]}"
        return "dependabot", ""
    return "review", ""


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(argv[2], encoding="utf-8") as fh:
        pr = json.load(fh)
    with open(argv[3], encoding="utf-8") as fh:
        files = [json.loads(line) for line in fh if line.strip()]
    kind, reason = shape(argv[1], pr, files)
    if kind is None:
        print(reason, file=sys.stderr)
        return 1
    print(kind)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
