# ctx interface (api 1)

ctx: a markdown context store for coding agents.

usage: ctx [--json] [--store <path>] [--now <timestamp>] [--stdin] <verb> [arguments]
       ctx memory        one memory-tool call as JSON on stdin
       ctx mcp           MCP server on stdin and stdout
       ctx help [<verb> | <topic>]
       ctx --version

Memory tool   view create str_replace insert delete rename
Writes        log fm row new move
Reads         brief find resolve get
Maintenance   validate doctor maintain touch migrate init

Topics        exit-codes errors output environment stores backends markdown-backend
              docs selectors budgets payloads front-ends

Available in this version: every verb except row.

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
| `NO_MATCH` | 2 | text not found in the doc | the doc key |
| `DOC_EXISTS` | 3 | doc exists | the doc key |
| `AMBIGUOUS_SELECTOR` | 3 | selector matches more than one target | the selector |
| `SCHEMA_VIOLATION` | 3 | schema violation | the field or file |
| `SECRET_DETECTED` | 3 | payload looks like a secret | the rule that matched, never the value |
| `PATH_ESCAPE` | 3 | path leaves the store | the path |
| `NOT_OWNER` | 3 | doc is owned by another actor | the doc key |
| `GENERATED` | 3 | doc is generated | the doc key |
| `MIGRATION_PENDING` | 3 | doc is behind its type's schema version | the doc key |
| `UNAUDITED_WRITE` | 3 | doc changed with no audit row | the doc key |
| `LOCK_TIMEOUT` | 4 | lock timeout | the lock |
| `STORE_READONLY` | 5 | store is read-only or unwritable | the store path |
| `GIT_FAILED` | 5 | version control refused the commit | the git command that failed |
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
| `CTX_STORE` | walk up from the working directory | A store locator, or a list separated like `PATH`; `--store` overrides it (`ctx help stores`) |
| `CTX_NO_WALK` | unset | Markdown backend. `1` turns the walk up off: without `CTX_STORE` or `--store` the result is `NO_STORE`. For tests, worktrees and temporary directories below a live store |
| `CTX_ACTOR` | `$USER` | Who writes: the name of the audit file and of every row in it. 1 to 64 of letters, digits, `.`, `_`, `-`; the first a letter or digit. Over `ctx mcp`, a write tool's `actor` stands in for it where the store allows the name |
| `CTX_LOCK_MODE` | probed | Markdown backend. `flock` or `mkdir` forces the lock mode |
| `CTX_LOCK_TIMEOUT` | `10` | Seconds a write waits for the lock before exit 4 |
| `CTX_CACHE_DIR` | `$XDG_CACHE_HOME/ctx`, else `~/.cache/ctx` | Optional cache; never required, never created by a read |
| `CTX_SCRATCH` | unset | Directory for temporary payload files |
| `CTX_GIT` | unset | Markdown backend. `1` lets `maintain` commit the store's changes; never a commit per write |

Nothing else is read from the environment (`USER` and `LOGNAME` name the
actor when `CTX_ACTOR` is unset), and nothing from `$HOME` except the cache
directory.

## Stores

The interface is the contract: verbs, errors and the doc model. A store keeps
the docs behind it, in a backend. Markdown files are the default backend; the
same calls give the same answers on any other.

A store is named by a locator: a directory path (a Markdown store), or
`<scheme>://<rest>`. `CTX_STORE` takes one locator or a list separated like
`PATH`. An unknown scheme, or a locator that names no store, is `NO_STORE`.

| Backend | Locator | Keeps docs |
|---|---|---|
| `markdown` | a path, or `markdown://<path>` | as `*.md` files in a directory (`ctx help markdown-backend`) |
| `memory` | `memory://<name>` | in the process; for tests of callers, and the reference for writing a backend |

A write runs only on a store named by `CTX_STORE` or `--store`; on a store
found without being named it fails with `STORE_NOT_NAMED`. Reads are not
audited and not gated: that is a decision, not a gap.

With a list of stores, a read looks in every store in order (a doc key found
in an earlier store hides the same key in a later one; rows carry the store's
number, `2:reference/x`). A write goes to the first store that holds the doc
it names, else to the first store.

Every store has settings, whatever the backend:

| Setting | Meaning |
|---|---|
| `schema_version` | The version of the store's layout and schemas |
| `generated` | Glob patterns over doc keys (`INDEX.md`): docs another tool writes. Not validated, not writable (`GENERATED`) |
| `ignore` | Glob patterns: not docs. Every verb answers `NO_SUCH_DOC` for them |
| `resolve` | `key_regex`, `fields`, `section` (`ctx help resolve`) |
| `maintain` | `size_guard`, `keep_log`, `archive`, `session_days`, `session_archive`, `catalog`, `git_debounce` (`ctx help maintain`) |
| `mcp` | `actors`: a regular expression; an MCP write may name an actor it matches in full (`ctx mcp`), none when it is absent |

Every write leaves one audit row: `seq` (the store's write counter) `ts`
`actor` `verb` `doc` `before` `after` (sha256 of the doc, `null` for none).

## Backends

A backend provides: the settings, the type schemas and templates, the doc
keys, read, read of a doc's head (optional: for scans that need the
frontmatter only), write (whole or not at all), remove, a lock with a timeout, the
audit rows, whether the store is read-only, and what `doctor` reports. The
core does the rest: validation, the secret guard, owners, sections, budgets,
links. `ctxstore/backend.py` is the interface; its `memory` backend is the
shortest complete example.

The core is standard library only. A backend that needs a driver is a
package of its own.

## Markdown backend

A store root is a directory that holds `ctx-store.json`, the settings:

    {"schema_version": 1}

`ctx init` makes one, with the settings, schemas and templates a consumer
hands over (`ctx help init`).

Without `CTX_STORE` or `--store`, ctx walks up from the working directory and
takes the first directory that is a store root or holds a `.context/`
directory that is one. A directory without the marker never matches.
`CTX_NO_WALK=1` turns the walk off. A store found by the walk is not named:
reads work, writes fail with `STORE_NOT_NAMED`.

| Path | Holds |
|---|---|
| `ctx-store.json` | `schema_version`, and optionally `generated` and `ignore`: lists of glob patterns over doc paths. A generated doc is not validated and not writable (`GENERATED`); an ignored one is not a doc: every verb answers `NO_SUCH_DOC` for it. `resolve`: `key_regex`, `fields`, `section` (see `ctx help resolve`). `mcp`: `actors`, a regular expression; an MCP write may name an actor it matches in full (`ctx mcp`), none when it is absent |
| `**/*.md` | The docs. A doc's key is its path without `.md` (`reference/lock-modes`) |
| `.ctx/types/<type>.json` | One schema per doc type |
| `.ctx/templates/<type>.md` | The scaffold of a new doc of the type; `{{TITLE}}` `{{TYPE}}` `{{DATE}}` `{{KEY}}` `{{SLUG}}` are filled in |
| `.audit/<actor>.jsonl` | One row per write: `seq` (the store's write counter) `ts` `actor` `verb` `doc` `before` `after` (sha256 of the doc, `null` for none) |
| `.audit/seq` | The number of the last write |
| `.lock/` | The store lock |

Directories whose name starts with a dot hold no docs.

Files can change without ctx. `validate --changed` finds that
(`UNAUDITED_WRITE`); `--adopt` records it.

The lock is `flock` where a probe proves it, an atomic `mkdir` lock
otherwise (`ctx help doctor`). A write is a temp file in the doc's directory,
fsync, rename, then a re-read that must give the same bytes. Reads write
nothing under the store.

## Docs

A doc starts with frontmatter between two `---` lines: one `key: value` per
line, the value a scalar or an inline list `[a, b]`. Nested and multi-line
values are violations. The doc's type is its `type` field, or the type whose
schema names its path.

A type schema is a JSON object (in a Markdown store, `.ctx/types/<type>.json`);
every key is optional:

| Key | Meaning |
|---|---|
| `paths` | Glob patterns: docs at these paths are of this type when they have no `type` field |
| `frontmatter` | Field → rule: `required`, `kind` (`string` `list` `date` `timestamp`), `const`, `enum` |
| `sections` | `##` headings every doc of the type has |
| `log` | `section`: where `log` adds its entry; `log` rules without it make `maintain` and `migrate`'s `log_order` use the body-level dated list instead (the `- <date> — …` lines after the `#` title and before the first `##`); `order`: `oldest-first` (default, the entry goes last) or `newest-first` (the entry goes first); `grammar`: a regular expression every line of that section matches; `ledger: true`: `log` appends raw lines at the end of the doc |
| `owner` | The field that names the doc's owner; a write by another actor fails with `NOT_OWNER` |
| `version` | The type's schema version, a number; 0 or absent: the type has no versions |
| `migrations` | One step per version, in order; step `n` takes a doc from version `n-1` to `n` (`ctx help migrate`) |

A doc of a versioned type carries `schema_version: <type>.v<n>`; without the
field it is at version 0. `new`, `create --type`, `fm` and `migrate` write the
field. A doc behind its type is `MIGRATION_PENDING`: `validate` reports it
and a write to it fails, until `migrate --apply` has run. A doc ahead of its
type, or with a stamp that is not its type's, is `SCHEMA_VIOLATION
schema_version`, for `validate` and for every write; `fm <doc>
schema_version <stamp>` is the one write that may correct it.

A schema that cannot be applied (not an object, a key of the wrong shape, a
`grammar` that is not a regular expression) fails every verb on the store
with `SCHEMA_VIOLATION .ctx/types/<type>.json`.

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

## Front-ends

One core, three front-ends. Each ends in the same code, so a write is
validated, locked and audited whichever way it arrives.

| Front-end | Use |
|---|---|
| `ctx <verb>` | hooks, people, cron |
| `ctx memory` | Anthropic's memory tool: the tool call's input as one JSON object on stdin (`{"command": "view", "path": "/memories/epics/sample.md"}`), the tool result on stdout. `/memories` is the store. Commands `view` `create` `str_replace` `insert` `delete` `rename` are the verbs of the same name |
| `ctx mcp` | MCP server over stdio (JSON-RPC 2.0, one message per line): one tool per built verb but `init`, `ctx_<verb>`, its input the verb's parameters. A tool's description is the verb's help, followed by what differs over MCP: the names of the parameters the help shows in angle brackets, in order, and the options that are not offered. A failure is a tool result with `isError` and the error line. `--from` and `--out` are not offered: the server's files are not the client's. Every tool but the reads (`brief` `get` `find` `resolve` `view` `doctor`) takes an optional `actor`: the write runs as that actor (owner check, audit file and row) when it has the `CTX_ACTOR` form and every store opened allows it in `ctx-store.json` (`mcp.actors`); otherwise `USAGE actor` and nothing is written. One server can so serve several sessions, each writing as itself. The HTTP connector offers no `actor`: it is one identity. Protocol versions below. |

MCP protocol versions, the same for `ctx mcp` and the HTTP connector
(`ctx-serve`, `docs/connector.md`). Both eras are served in one process,
on one endpoint; nothing is kept between requests in either.

| Era | Versions | How a request is served |
|---|---|---|
| stateless | `2026-07-28` | the request names its version in `params._meta["io.modelcontextprotocol/protocolVersion"]` and carries `io.modelcontextprotocol/clientCapabilities`; no `initialize`. Methods `server/discover`, `tools/list`, `tools/call`; any other method is `-32601`. Every result has `resultType: "complete"` and `_meta["io.modelcontextprotocol/serverInfo"]`; `server/discover` and `tools/list` add `ttlMs: 0` and `cacheScope: "private"` |
| handshake | `2025-11-25` `2025-06-18` `2025-03-26` `2024-11-05` | a request without that `_meta` version, or naming one of these, and every `initialize`, whatever its `_meta` or header names: the method itself selects the handshake, so a client that falls back to it is never refused over the version. `initialize` agrees the version: one this server does not know gets `2025-11-25`. Methods `initialize`, `ping`, `tools/list`, `tools/call`; results as before, without `resultType` |

The refusals below never apply to `initialize` (the version it names is agreed, not checked).

| Refusal | JSON-RPC error | HTTP | Log reason |
|---|---|---|---|
| a version this server does not speak, in `_meta` or in the `MCP-Protocol-Version` header | `-32022`, `data.supported` (the versions above, newest first) and `data.requested` | 400 | `protocol-version` |
| `_meta` lacks the version or the client capabilities, or the header names `2026-07-28` and `_meta` no version | `-32602` | 400 | `meta-missing` |
| the `_meta` version is not a string | `-32602` | 400 | `meta-invalid` |
| HTTP, stateless: `MCP-Protocol-Version`, `Mcp-Method` or (`tools/call`) `Mcp-Name` missing or not the body's value; `Mcp-Name` may be `=?base64?…?=` | `-32020` | 400 | `header-mismatch` |
| stateless: a method not served | `-32601` | 404 | `method-unknown` |

Over HTTP a notification is answered `202`, or `400` with `-32022` and no id when its
`MCP-Protocol-Version` header names a version this server does not speak. Authentication is
checked before any of this: a request without it is `401`.

Claude Desktop, `claude_desktop_config.json`:

    {"mcpServers": {"ctx": {"command": "/path/to/ctx", "args": ["mcp"],
      "env": {"CTX_STORE": "/path/to/store", "CTX_ACTOR": "desktop"}}}}

## Verbs

### view

ctx view [<doc> | <directory>] [--range <first>:<last>] [--budget <bytes>] [--full]

A doc with its lines numbered, or the docs below a directory with their
sizes (no argument: the whole store). Inside the byte budget (default 8192).

Exit 0; 2 `NO_SUCH_DOC`; 3 `PATH_ESCAPE`.

### create

ctx create <doc> [<text>] [--from <file>] [--type <type>] [--title <title>]

Write a doc: the text given, or without one the scaffold of `--type`. An
existing doc is replaced (the audit row keeps the hash of what it was); use
`new` to refuse that.

Exit codes as for `log`.

### str_replace

ctx str_replace <doc> --old <text> [--new <text>]

Replace one exact string. It must occur once: no match is `NO_MATCH`, more
than one `AMBIGUOUS_SELECTOR`.

Exit codes as for `log`; 2 `NO_MATCH`.

### insert

ctx insert <doc> <text> --line <n> [--from <file>]

Insert text after line `n` (0: before the first line).

Exit codes as for `log`; 1 `USAGE --line` for a line the doc does not have.

### delete

ctx delete <doc>

Remove a doc. The audit row records it (`after` is `null`). Docs that link
to it are named in the result and left as they are.

Exit codes as for `log`.

### rename

ctx rename <doc> <to>

Rename a doc and rewrite the links to it: relative markdown links
(`[x](../a/b.md#part)`) and wikilinks of its key or name (`[[b]]`). The
doc's own relative links are rewritten for its new place. One lock, one
audit row per doc touched.

Exit codes as for `log`; 3 `DOC_EXISTS`.

### log

ctx log <doc> <text> [--section <heading>] [--date <YYYY-MM-DD>] [--from <file>]

Add one entry to a doc's log, under the store lock. The entry is
`- <date> — <text>`. It goes after the section's last line, so a log reads
oldest first, unless the doc's type says `"order": "newest-first"`: then it
goes before the first entry. The section is `--section`, else the
`log.section` of the doc's type.
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

ctx new <type> <doc> [--title <title>]

Scaffold a doc from the type's template, `.ctx/templates/<type>.md`; a type
without one gets frontmatter and a title.

Exit codes as for `log`; 3 `DOC_EXISTS`.

### move

ctx move <doc> <to>

`rename` under the name of the structured write; the audit rows say `move`.

### brief

ctx brief <doc> [--links] [--near] | --registry | --session <id> [--budget <bytes>] [--full]

The cold-start read, within a byte budget (default 4096; above 8192 needs
`--full`). What does not fit is replaced by one line that says how much is
missing.

- `<doc>`: its frontmatter, its sections with their sizes, the 5 newest
  entries of its log.
  With `--links`, also the docs it links to and the docs that link to it
  (relative markdown links and wikilinks). That reads every doc of the store.
  With `--near`, one line for each of those docs: `→` for a doc it links
  to, `←` for a doc that links to it, then `key · title · updated ·
  summary`. The doc and what is around it, inside the one budget. It
  reads every doc of the store too, with or without `--links`.
- `--registry`: one line per session that has not ended: name, status, epic,
  what it is working on, heartbeat.
- `--session <id>`: that session's doc, as for `<doc>`, then its body.

Exit 0; 2 `NO_SUCH_DOC`; 3 `AMBIGUOUS_SELECTOR`.

### find

ctx find [<query>] [--type <type>] [--tag <tag>] [--where <conditions>] [--budget <bytes>] [--full] [--out -|auto]

One row per hit, best first: `key · title · updated · summary` (≤ 80
characters), then `· § <section>` when one `##` section holds the terms.

The query is terms: words, and phrases in double quotes; punctuation around
a word is dropped. A doc is a hit when every term occurs in its key or its
text. A query of four terms or more is read as a task, not a lookup: a doc
that holds at least 35 % of the query's weight is a hit too, listed behind the
docs that hold every term, its row ending in `· <n> of <m> terms`. A term's
weight is how few docs hold it; a word that no doc holds, or every doc, weighs
nothing. Matching folds case, ignores a
plural `s` on a word and the `-` and `_` inside words: `orders` finds `Order
model`, `rollout` finds `roll-out`. It does not know synonyms.

`--where` keeps the docs whose frontmatter meets every condition, separated
by commas: `field=value`, `field!=value`, `field>=value`, `field<=value`
(`status=active,updated>=2026-01-01`). A list field meets `=` when it holds
the value. `>=` and `<=` compare as text, which orders dates and timestamps.
A doc without the field meets only `!=`. A value cannot hold a comma or start with `=`. A query
is not needed with `--where`,
`--type` or `--tag`.

Hits are ranked by where each term occurs (key and title, then tags and
headings, then the rest), by how few docs hold the term, and higher when the
terms occur as one phrase or in one section. Ties go by key. The 300 best
candidates are ranked this way; hits past them follow in key order.

Hits that do not fit the budget (default 4096) are folded into a count.
`--json` adds `rows`: `doc` `title` `updated` `summary` `section` `score`
`terms` `of` for every hit shown. `--out auto` writes the whole result to a file in
`$CTX_SCRATCH` and prints its path.

find is a scan: there is no index to build or to lose. Its cost grows with
the store and with the number of terms.

Exit 0 (no hit is `0 hits`); 1 `USAGE`.

### resolve

ctx resolve <key>

The one doc a tracker key belongs to, as a `find` row. The key's shape is the
store's `resolve.key_regex`, never a built-in pattern. A doc matches when one
of its `resolve.fields` is the key, else when its `resolve.section` names
the key; field matches win.

Exit 0; 1 `USAGE resolve.key_regex` when no store sets one; 2 `NO_SUCH_DOC`
(also for a key of another shape); 3 `AMBIGUOUS_SELECTOR`, listing the docs.

### get

ctx get <doc>[,<doc>…] [--section <heading>] [--tail <n>] [--budget <bytes>] [--full] [--out -|auto]

One section of a doc, or its body; `--tail` keeps the last `n` entries
(lines that are neither blank nor comments). Inside the byte budget
(default 8192); `--out auto` as for `find`.

Several docs, separated by commas, are answered in one call, each under a
line `== <doc> ==`. A key that holds a comma itself and names a doc that
exists is read as that one doc; it is looked up first. The budget is the call's and is shared evenly, so one long doc cannot
crowd the others out. A doc that lacks the section says so in its part. A doc
that does not exist, or that has the heading twice, fails the call.

Exit 0; 2 `NO_SUCH_DOC`, `NO_SUCH_SECTION`; 3 `AMBIGUOUS_SELECTOR`.

### validate

ctx validate [--changed] [--adopt]

Check every doc against the general rules and the schema of its type.

`--changed` looks only at docs that differ from what their last audit row
recorded, and reports each as `UNAUDITED_WRITE`: it changed outside ctx.
`--changed --adopt` records the current state of every such doc that is
valid instead (an audit row with the verb `adopt`), and reports only the
invalid ones. A doc deleted outside ctx is adopted as deleted (`after`
`null`). `--adopt` is a write.

A doc larger than the store's `maintain.size_guard` (default 30000 bytes) is
a warning, never a failure: `warning: SIZE_GUARD <doc>: <n> bytes` after the
result, `warnings` in the envelope.

Exit 0 with the number of docs checked; 3 with one line per finding.

### doctor

ctx doctor [--json] [--store <locator>]

Report what this machine and store give the tool: ctx version and api, Python
version, lock timeout, git switch, scratch and cache directory, how the store
was found (`flag`, `env` or `walk`), and per store its backend, its locator
and what the backend reports. A Markdown store reports the schema version,
filesystem type, read-only state and lock mode.

Lock mode is `flock` where a probe proves it (two descriptors on the marker
file, the second must be refused), `mkdir` on a writable store without flock,
`none` on a read-only store without flock. doctor writes nothing.

Exit 0 with the report; 2 `NO_STORE`; 3 `SCHEMA_VIOLATION ctx-store.json`.

### maintain

ctx maintain

The periodic pass, for an asynchronous hook, cron or the end of a session.
A second run changes nothing.

1. Log tails: a doc over `size_guard` bytes (default 30000) keeps the
   `keep_log` newest entries of its log (default 20); the older ones move to
   the doc `archive` names (default `archive/{slug}-log`), which is created
   if it does not exist yet. The archive doc's own type (default `log`)
   decides the shape: with `log.section` the moved entries go into that
   section; with `log` rules but no `section`, into its body-level dated
   list; with no `log` rules, into a `## Log` section; `log.order`
   `newest-first` puts the moved block at the top, newest first,
   `oldest-first` (default) at the end, oldest first, as they are kept. A
   freshly created archive doc carries `title`, `type`, `updated` plus a
   reasonable default for every other field its type requires: `domain`
   copied from the source doc, an enum field's first value, today's date for
   a `date` field — the first of these the field's rule accepts, so a source
   `domain` outside the rule's enum falls back to the enum. A required field
   with no derivable default is reported as
   `archive: <target> — cannot create, <field> required` and that tail is
   left alone: nothing is written to either doc.
2. Sessions: a session doc with `status: ended` and a heartbeat older than
   `session_days` (default 7) moves to `session_archive` (default
   `sessions/archive/{name}`), links to it rewritten; its audit rows are put
   aside and still count.
3. Catalog: when `catalog` names a generated doc, it is rewritten as a table
   of every doc (key, title, type, status, updated), only if it changed.
4. With `CTX_GIT=1` on a store inside a git work tree: one commit of the
   store's changes, author and committer the actor, unless ctx committed
   less than `git_debounce` seconds ago (default 300).

Exit codes as for `log`.

### touch

ctx touch --session <id> [--working <text>]

Set `heartbeat` to now in the session doc whose `session_id` or `session` is
`<id>`, and `working_on` when `--working` is given. The write is recorded for
the session itself, whatever `CTX_ACTOR` says.

Exit 0; 2 `NO_SUCH_DOC`; 3 `AMBIGUOUS_SELECTOR`; otherwise as for `log`.

### migrate

ctx migrate --check | --dry-run | --apply

Take every doc that is behind its type's schema version to that version.

A type's `migrations` are numbered steps. A step may hold `rename_fields`
and `rename_sections` (old → new), `set_fields` (field → value),
`remove_fields`, `log_order` (`oldest-first` or `newest-first`: a log whose
dated entries read the other way round is reversed; one already in order, or
in no order, is left alone; with no `log.section` this reorders the doc's
body-level dated list instead), `log_order_from` (beside `log_order`, the other
order: the one the logs were written in, so entries whose dates all tie, one
busy day, are reversed too; a log whose dates read in the target order is
still left alone) and `replace_comments` (`old`, `new`: replaced inside HTML
comments only). Steps touch frontmatter, section names and the order of log
entries; prose is never rewritten. A doc takes each step once, so a second
run changes nothing.

- `--check`: exit 3 `MIGRATION_PENDING`, one line per doc, when steps are
  pending.
- `--dry-run`: what `--apply` would do, per doc: versions, steps, lines
  added or removed. Writes nothing.
- `--apply`: one audited write per doc, validated at the new version. Every
  doc is checked before any is written: when one would not be valid, the run
  writes nothing and names each such doc, one line per finding, as `validate`
  does (`SCHEMA_VIOLATION <doc> <detail>`; `--json` findings carry `doc`).

Exit 0; 3 `MIGRATION_PENDING` (`--check`), `SCHEMA_VIOLATION` when a doc is
not valid after its steps; otherwise as for `log`.

### init

ctx init --store <path> [--settings <file>] [--types <folder>] [--templates <folder>] [--replace | --upgrade]

Make a Markdown store, or bring an existing one's settings, type schemas and
templates to what a consumer hands over. The store is the one path of
`--store` or `CTX_STORE` (or `markdown://<path>`); the directory and its
parents are created. Another backend, or a list of stores, is `USAGE`; no
store named is `STORE_NOT_NAMED`.

- `--settings`: a JSON object of `generated`, `ignore`, `resolve`, `maintain`
  and `mcp` (`ctx help stores`), and `schema_version` if given the current
  one, 1. It becomes `ctx-store.json`. Without it a new store gets
  `{"schema_version": 1}` and an existing one keeps its marker.
- `--types`: a folder of `<type>.json` schemas, copied to `.ctx/types/`.
- `--templates`: a folder of `<type>.md` scaffolds, copied to `.ctx/templates/`.

A type name is lower-case letters, digits, `.`, `_` and `-`, the first a
letter or digit; anything else in the folder is `USAGE <file>`. Every input
is checked, as a store checks its marker and schemas when it opens, before
anything is written: one that is not valid is `SCHEMA_VIOLATION <file>` (a
settings key the store does not know: `SCHEMA_VIOLATION <key>`).

A store file that holds the same content already is left alone (the marker:
the same settings, however laid out), so a second run writes nothing. One
that holds other content is `SCHEMA_VIOLATION <store file>`
(`.ctx/types/epic.json`) and nothing is written, unless `--replace`: then it
is overwritten. Files the run does not name are left alone.

`--upgrade` is for a consumer's next version: a file that differs is
replaced only while it holds what `init` last wrote there (the `after` of
its latest `init` audit row), and kept otherwise, as someone edited it
since: `kept` in the result, exit 0. A file that already holds the same
content and has no `init` row yet, as in a store made by hand, gets one
(`before` = `after`), the baseline for the next upgrade. `--upgrade` with
`--replace` is `USAGE`.

A new store's marker is written first, then the rest under the store lock.
One audit row per file written, verb `init`, `doc` the store file;
`validate --changed` passes over them. The result names each file `written`,
`unchanged` or `kept`; `--json` gives `store`, `written`, `unchanged`, and
`kept` with `--upgrade`. Not offered over MCP.

Exit 0; 1 `USAGE`; 3 `SCHEMA_VIOLATION`; 4 `LOCK_TIMEOUT`; 5
`STORE_READONLY` (a directory it cannot write), `STORE_NOT_NAMED`.
