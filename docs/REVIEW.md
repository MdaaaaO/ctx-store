# Review rules

What the reviewer (`.github/workflows/claude-review.yml`, or a person) holds a pull request to. The
workflow reads this file from the base branch, so a PR cannot change the rules it is judged by.

## 1. What CI already decides

Never repeat these in a review: the test suite on every supported Python, on the host and in a
container with a read-only store (`ci`); the title's form (`pr-title`); the linked issue (`pr-issue`).

## 2. Lenses

Apply each to every changed line.

| Lens | The question | Typical finding |
|---|---|---|
| Contract | Does the code still do what `ctxstore/interface.md` says, and does the spec say what the code does? | An exit code, error string, envelope field or option changed in one and not the other; a field removed or re-meant without an `api` bump |
| Store safety | Can this lose, corrupt or leak what is in a store? | A write outside lock → validate → secret scan → temp file + rename → checksum → audit row; a read that writes under the store; a path that can leave the store; a secret value echoed in an error or a log |
| Boundaries | Does the core stay small? | An import outside the standard library; network access; filesystem access outside `ctxstore/fs.py`; a prompt, colour or non-deterministic ordering in output; anything read from `$HOME` other than the cache directory |
| Errors | Does every failure end in one fixed string and the right exit code? | A swallowed exception; a traceback reaching the user; an empty result that could also be a failed call |
| Tests | Would a regression in this change turn a test red? | A new verb or option with no golden output; a failure path with no test; a test that can reach a store outside its own fixture |
| Docs | Is what a reader finds still true? | README, `interface.md`, `CONTRIBUTING.md` or a comment describing the old behaviour |

## 3. Grading

| Grade | Meaning | Rule |
|---|---|---|
| `STOP` | Must be fixed before merge | Quotes the offending line **and** names the rule above it breaks. Without both it is a `WARN`. |
| `WARN` | Needs an answer before merge | Phrased as a question the author can answer or fix |
| `NIT` | Optional | Never blocks |

Not findings: prose style, naming taste, lines the PR does not touch.

## 4. Finding shape

One line per finding: `[STOP|WARN|NIT] path:line — claim (rule)`.

## 5. Verdict

`approve` only when no `STOP` and no `WARN` is open on the PR at the reviewed head, earlier unfixed
findings included. Otherwise `request-changes`. What could not be verified is said in the summary.
