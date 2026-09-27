# ctx interface (api 1)

ctx: a markdown context store for coding agents.

usage: ctx [--json] [--store <path>] <verb> [arguments]
       ctx help [<verb> | <topic>]
       ctx --version

Memory tool   view create str_replace insert delete rename
Writes        log fm row new move
Reads         brief find resolve get
Maintenance   validate doctor maintain touch migrate

Topics        exit-codes errors output environment store selectors budgets payloads

Available in this version: doctor, help.

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
| `CTX_LOCK_TIMEOUT` | `10` | Seconds a write waits for the lock before exit 4 |
| `CTX_CACHE_DIR` | `$XDG_CACHE_HOME/ctx`, else `~/.cache/ctx` | Optional cache; never required, never created by a read |
| `CTX_SCRATCH` | unset | Directory for temporary payload files |
| `CTX_GIT` | unset | `1` turns on debounced git commits of the store |

Nothing else is read from the environment, and nothing from `$HOME` except the
cache directory.

## Store

A store root is a directory that holds `ctx-store.json`:

    {"schema_version": 1}

Without `CTX_STORE` or `--store`, ctx walks up from the working directory and
takes the first directory that is a store root or holds a `.context/`
directory that is one. A directory without the marker never matches.
`CTX_NO_WALK=1` turns the walk off. Docs are `*.md` files with YAML frontmatter below the
root. Reads write nothing under the store.

## Selectors

A section is an exact `##` heading. Zero matches and several matches both
fail; unknown parameters are rejected.

## Budgets

Budgets are bytes (about 4 characters per token). A payload over 8 KB needs
`--full`.

## Payloads

`--from <file>` and JSON on stdin are accepted wherever a verb takes text.

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

ctx log <doc> — locked chronological append to a log section or ledger. Not built yet (P1).

### fm

ctx fm <doc> <field> <value> — schema-checked frontmatter set. Not built yet (P1).

### row

ctx row <doc> — add or change one table row. Built on first need.

### new

ctx new <type> <key> — scaffold a doc from its type's template. Not built yet (P2).

### move

ctx move <doc> <new> — move a doc and rewrite links to it. Not built yet (P2).

### brief

ctx brief [--registry | --session <id> | <doc>] — cold-start read under a byte budget. Not built yet (P1).

### find

ctx find --budget <bytes> — search that returns references and slices. Not built yet (P2).

### resolve

ctx resolve <key> — turn a key into a doc reference. Not built yet (P2).

### get

ctx get <doc> --section <heading> [--tail <n>] — read one section or its last lines. Not built yet (P2).

### validate

ctx validate [--changed] — check docs against their schema; report unaudited writes. Not built yet (P1).

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

ctx touch --session <id> — refresh a session's heartbeat. Not built yet (P1).

### migrate

ctx migrate — move a store to the current schema version. Not built yet (P3).
