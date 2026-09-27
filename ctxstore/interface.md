# ctx interface (api 1)

ctx: a markdown context store for coding agents.

usage: ctx [--json] [--store <path>] [--now <timestamp>] [--stdin] <verb> [arguments]
       ctx help [<verb> | <topic>]
       ctx --version

Memory tool   view create str_replace insert delete rename
Writes        log fm row new move
Reads         brief find resolve get
Maintenance   validate doctor maintain touch migrate

Topics        exit-codes errors output environment store docs selectors budgets payloads

Available in this version: brief, doctor, fm, help, log, touch, validate.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | usage |
| 2 | not found |
| 3 | validation |
| 4 | lock timeout |
| 5 | store read-only or unwritable |

## Errors

Every failure prints one line on stderr, `<CODE>[ <detail>]: <message>`, and
exits with the code's exit status. With `--json` the error object is also
printed on stdout.

| Code | Exit | Message | Detail |
|---|---|---|---|
| `USAGE` | 1 | bad command line | the offending argument or variable |
| `NOT_BUILT` | 1 | verb is specified but not built in this version | the verb |
| `NO_STORE` | 2 | no store found | the path, when one was given |
| `NO_SUCH_DOC` | 2 | no such doc | the doc key |
| `NO_SUCH_SECTION` | 2 | no such section | the heading |
| `AMBIGUOUS_SELECTOR` | 3 | selector matches more than one target | the selector |
| `SCHEMA_VIOLATION` | 3 | schema violation | the field or file |
| `SECRET_DETECTED` | 3 | payload looks like a secret | the rule that matched, never the value |
| `PATH_ESCAPE` | 3 | path leaves the store | the path |
| `NOT_OWNER` | 3 | doc is owned by another actor | the doc key |
| `GENERATED` | 3 | doc is generated | the doc key |
| `UNAUDITED_WRITE` | 3 | doc changed with no audit row | the doc key |
| `LOCK_TIMEOUT` | 4 | lock timeout | the lock |
| `STORE_READONLY` | 5 | store is read-only or unwritable | the store path |
| `STORE_NOT_NAMED` | 5 | a write needs CTX_STORE or --store | — |

`validate` may report several failures: one line each on stderr, the doc
before the detail (`SCHEMA_VIOLATION epics/sample status: …`), and with
`--json` the list as `error.findings` (`code`, `detail`, `doc`, `message`).
The exit status and `error.code` are the first one's.

## Output

- Plain text by default; `--json` prints one envelope on stdout.
- Success: `{"api": 1, "ok": true, "verb": "<verb>", "data": {...}}`.
- Failure: `{"api": 1, "ok": false, "error": {"code", "detail", "message", "exit"}}`.
- `api` changes only when a field is removed or changes meaning.
- Keys are sorted and lists have a defined order: the same input gives the same bytes.
- No prompts. No colour, on a terminal or off it; `NO_COLOR` has nothing to switch off.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `CTX_STORE` | walk up from the working directory | Store root, or a list separated like `PATH`; `--store` overrides it |
| `CTX_NO_WALK` | unset | `1` turns the walk up off: without `CTX_STORE` or `--store` the result is `NO_STORE`. For tests, worktrees and temporary directories below a live store |
| `CTX_ACTOR` | `$USER` | Who writes: the name of the audit file and of every row in it. Letters, digits, `.`, `_`, `-` |
| `CTX_LOCK_MODE` | probed | `flock` or `mkdir` forces the lock mode |
| `CTX_LOCK_TIMEOUT` | `10` | Seconds a write waits for the lock before exit 4 |
| `CTX_CACHE_DIR` | `$XDG_CACHE_HOME/ctx`, else `~/.cache/ctx` | Optional cache; never required, never created by a read |
| `CTX_SCRATCH` | unset | Directory for temporary payload files |
| `CTX_GIT` | unset | `1` turns on debounced git commits of the store |

Nothing else is read from the environment (`USER` and `LOGNAME` name the
actor when `CTX_ACTOR` is unset), and nothing from `$HOME` except the cache
directory.

## Store

A store root is a directory that holds `ctx-store.json`:

    {"schema_version": 1}

Without `CTX_STORE` or `--store`, ctx walks up from the working directory and
takes the first directory that is a store root or holds a `.context/`
directory that is one. A directory without the marker never matches.
`CTX_NO_WALK=1` turns the walk off.

A write runs only on a store named by `CTX_STORE` or `--store`; on a store
found by the walk it fails with `STORE_NOT_NAMED`. Reads keep the walk and
write nothing under the store.

| Path | Holds |
|---|---|
| `ctx-store.json` | `schema_version`, and optionally `generated` and `ignore`: lists of glob patterns over doc paths. A generated doc is not validated and not writable (`GENERATED`); an ignored one is not a doc |
| `**/*.md` | The docs. A doc's key is its path without `.md` (`reference/lock-modes`) |
| `.ctx/types/<type>.json` | One schema per doc type |
| `.audit/<actor>.jsonl` | One row per write: `seq` (the store's write counter) `ts` `actor` `verb` `doc` `before` `after` (sha256 of the doc, `null` for none) |
| `.audit/seq` | The number of the last write |
| `.lock/` | The store lock |

Directories whose name starts with a dot hold no docs.

## Docs

A doc starts with frontmatter between two `---` lines: one `key: value` per
line, the value a scalar or an inline list `[a, b]`. Nested and multi-line
values are violations. The doc's type is its `type` field, or the type whose
schema names its path.

A type schema is a JSON object; every key is optional:

| Key | Meaning |
|---|---|
| `paths` | Glob patterns: docs at these paths are of this type when they have no `type` field |
| `frontmatter` | Field → rule: `required`, `kind` (`string` `list` `date` `timestamp`), `const`, `enum` |
| `sections` | `##` headings every doc of the type has |
| `log` | `section`: where `log` appends; `grammar`: a regular expression every line of that section matches; `ledger: true`: `log` appends raw lines at the end of the doc |
| `owner` | The field that names the doc's owner; a write by another actor fails with `NOT_OWNER` |

A doc of a type without a schema gets the general checks only: frontmatter
parses, a type is known, no `##` heading appears twice.

## Selectors

A section is an exact `##` heading. Zero matches and several matches both
fail; unknown parameters are rejected.

## Budgets

Budgets are bytes (about 4 characters per token). A payload over 8 KB needs
`--full`.

## Payloads

`--from <file>` reads a verb's text from a file (`-`: stdin). `--stdin` reads
all of a verb's parameters as one JSON object from stdin, keyed by parameter
name (`{"doc": "epics/sample", "text": "…"}`), so nothing passes through shell
quoting. Unknown keys are rejected.

`--now <YYYY-MM-DDTHH:MM:SSZ>` sets the time a write records, for
reproducible runs.

## Verbs

### view

ctx view <doc> — memory tool: show a doc or a directory listing. Not built yet (P2).

### create

ctx create <doc> — memory tool: create a doc from its type's template. Not built yet (P2).

### str_replace

ctx str_replace <doc> — memory tool: replace one exact string in a doc. Not built yet (P2).

### insert

ctx insert <doc> — memory tool: insert text at a line. Not built yet (P2).

### delete

ctx delete <doc> — memory tool: delete a doc, keeping links and the audit trail. Not built yet (P2).

### rename

ctx rename <doc> <new> — memory tool: rename a doc, keeping links and the audit trail. Not built yet (P2).

### log

ctx log <doc> <text> [--section <heading>] [--date <YYYY-MM-DD>] [--from <file>]

Append one entry to a doc's log, under the store lock. The entry is
`- <date> — <text>`, added after the section's last line: a log reads oldest
first. The section is `--section`, else the `log.section` of the doc's type.
For a ledger type the text is appended as it is, at the end of the doc.

The text is one line. `log` does not change the `updated` field: the time of
a doc's last write is the `ts` of its last audit row.

Exit 0; 2 `NO_SUCH_DOC`, `NO_SUCH_SECTION`; 3 `AMBIGUOUS_SELECTOR`,
`SCHEMA_VIOLATION`, `SECRET_DETECTED`, `PATH_ESCAPE`, `GENERATED`,
`NOT_OWNER`; 4 `LOCK_TIMEOUT`; 5 `STORE_READONLY`, `STORE_NOT_NAMED`.

### fm

ctx fm <doc> <field> <value> [--from <file>]

Set one frontmatter field, checked against the schema of the doc's type. A
list field takes `a, b` or `[a, b]`. Only that line of the doc changes, and
`updated` is set to today when the type has that field.

Exit codes as for `log`.

### row

ctx row <doc> — add or change one table row. Built on first need.

### new

ctx new <type> <key> — scaffold a doc from its type's template. Not built yet (P2).

### move

ctx move <doc> <new> — move a doc and rewrite links to it. Not built yet (P2).

### brief

ctx brief <doc> | --registry | --session <id> [--budget <bytes>] [--full]

The cold-start read, within a byte budget (default 4096; above 8192 needs
`--full`). What does not fit is replaced by one line that says how much is
missing.

- `<doc>`: its frontmatter, its sections with their sizes, the last 5
  entries of its log.
- `--registry`: one line per session that has not ended: name, status, epic,
  what it is working on, heartbeat.
- `--session <id>`: that session's doc, as for `<doc>`, then its body.

Exit 0; 2 `NO_SUCH_DOC`; 3 `AMBIGUOUS_SELECTOR`.

### find

ctx find --budget <bytes> — search that returns references and slices. Not built yet (P2).

### resolve

ctx resolve <key> — turn a key into a doc reference. Not built yet (P2).

### get

ctx get <doc> --section <heading> [--tail <n>] — read one section or its last lines. Not built yet (P2).

### validate

ctx validate [--changed] [--adopt]

Check every doc against the general rules and the schema of its type.

`--changed` looks only at docs that differ from what their last audit row
recorded, and reports each as `UNAUDITED_WRITE`: it changed outside ctx.
`--changed --adopt` records the current state of every such doc that is
valid instead (an audit row with the verb `adopt`), and reports only the
invalid ones. `--adopt` is a write.

Exit 0 with the number of docs checked; 3 with one line per finding.

### doctor

ctx doctor [--json] [--store <path>]

Report what this machine and store give the tool: ctx version and api, Python
version, lock timeout, git switch, scratch and cache directory, how the store
was found (`flag`, `env` or `walk`), and per store
the path, schema version, filesystem type, read-only state and lock mode.

Lock mode is `flock` where a probe proves it (two descriptors on the marker
file, the second must be refused), `mkdir` on a writable store without flock,
`none` on a read-only store without flock. doctor writes nothing.

Exit 0 with the report; 2 `NO_STORE`; 3 `SCHEMA_VIOLATION ctx-store.json`.

### maintain

ctx maintain — the periodic maintenance pass. Not built yet (P3).

### touch

ctx touch --session <id> [--working <text>]

Set `heartbeat` to now in the session doc whose `session_id` or `session` is
`<id>`, and `working_on` when `--working` is given. The write is recorded for
the session itself, whatever `CTX_ACTOR` says.

Exit 0; 2 `NO_SUCH_DOC`; 3 `AMBIGUOUS_SELECTOR`; otherwise as for `log`.

### migrate

ctx migrate — move a store to the current schema version. Not built yet (P3).
