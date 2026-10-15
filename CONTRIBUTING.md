# Contributing

## From issue to merge

| Step | What | Check |
|---|---|---|
| 1 | Open an issue (one `area:` label) | — |
| 2 | Branch off `main`: `<type>/<topic>` | — |
| 3 | `make ci` locally | `ci` runs the same on Python 3.10 and current, host and container |
| 4 | Open a PR: Conventional Commits title, `Closes #N` or `Refs #N` in the body, labels | `pr-title`, `pr-issue` |
| 5 | Review | `claude-review` posts findings and a verdict on every push |
| 6 | Squash-merge | `auto-merge` merges once every check is green, the review approves the head and no thread is open |

`main` takes squash-merged PRs only; `main-guard` opens an issue for anything that arrives another way.

## Commit and PR titles

`type(scope): description` — types `feat fix docs chore refactor test ci build perf style revert`,
lower-case description, no trailing period, at most 72 characters, the issue number inside
(`feat(core): add the locked log append (#3)`).

## Review

The rules are [`docs/REVIEW.md`](docs/REVIEW.md). Answer and resolve every thread: an open thread
blocks the merge. `@claude <question>` in a PR or issue comment gets an answer; `@claude review`
asks for a review of the current head. A PR from a fork, a draft and a PR that changes
`claude-review.yml` get no automatic review and wait for the maintainer. A release PR and a Dependabot
bump get none either: `auto-merge` merges them on green CI alone when their shape is exactly what the
tool wrote (the header of `.github/workflows/auto-merge.yml` says what it checks); any other shape waits
for the maintainer too.

## Labels

| Group | Labels |
|---|---|
| Area (one per issue and PR) | `area:core` `area:cli` `area:mcp` `area:docs` `area:ci` |
| Priority | `prio:1-now` `prio:2-next` `prio:3-later` |
| Flags | `breaking` `needs-design` `review-followup` `release` `dependencies` |

## Releases

Releases are cut with [conventional-release](https://github.com/MdaaaaO/conventional-release). A
release is a `chore(release): X.Y.Z` PR that adds the version's section to `CHANGELOG.md` and bumps
`ctxstore/VERSION`. `auto-merge` squash-merges it once CI is green, the title unchanged; the `release` workflow tags that commit
`vX.Y.Z`, publishes a GitHub Release, then builds the package from the tag, checks that the installed wheel
reports the version and publishes it to [PyPI](https://pypi.org/project/ctx-store/) as `ctx-store`. A failed
release job is re-run: each step does only what is missing, and the upload skips files PyPI already has.
To publish an existing tag again (a failed upload, a backfill) without a new commit:
`gh workflow run release.yml -f version=X.Y.Z` (or Actions → release → Run workflow). Release PRs need no
issue and get no automatic review; one that changes anything but `CHANGELOG.md` and `ctxstore/VERSION`
waits for the maintainer.

## Workflows and secrets

Third-party actions are pinned by commit SHA; Dependabot proposes the bumps, and `auto-merge` merges a
bump on green CI when every changed line is a `uses:` pin. Other changes under `.github/workflows/` need
the maintainer's review (`CODEOWNERS`).

| Secret | Used by | Without it |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude-review` | the review skips with a notice |
| `AUTOMERGE_TOKEN` (fine-grained PAT: this repo, Contents + Pull requests read/write) | `auto-merge` | the merge skips with a notice |

PyPI takes no secret: `release` publishes through trusted publishing (OIDC). Only its `pypi` job gets
`id-token: write`, and it runs in the `pypi` environment, which deploys from `main` only. PyPI's publisher
entry for `ctx-store` names this repo, `release.yml` and that environment; renaming the workflow or the
environment breaks the upload until the entry is changed to match.
