# ctx-store

`ctx` is a markdown context store for coding agents: one folder of `*.md` rows with YAML frontmatter as
the schema, a CLI with validated structured writes (`log`, `fm`, `new`, `move`), budgeted reads
(`brief`, `find --budget`, `resolve`, `get --section --tail`), a per-actor audit trail and a maintenance
pass (`init`, `validate`, `doctor`, `maintain`, `migrate`). The same core serves three front-ends: the CLI (Claude
Code hooks and skills call it), an Anthropic memory-tool handler (`view create str_replace insert delete
rename`) and an MCP server (stdio for Claude Desktop; HTTP with authentication for claude.ai, as a
separate package).

Production-grade by design: Python stdlib only (≥ 3.10), runs from a plain copy with no install, fixed
exit-code table, `--json` envelope (`"api": 1`), temp + rename writes under a lock, secret guard on every
payload, read-only store support, no writable state under the store for reads.

## Interface first

The interface is the contract: verbs, fixed errors and the doc model. Storage is a backend behind it.
Markdown files are the default backend, so a store stays a folder you can read and edit; the same calls
give the same answers on any other backend (`ctxstore/backend.py`, `ctx help backends`).

## Try it

```sh
git clone https://github.com/MdaaaaO/ctx-store && cd ctx-store
./ctx --version
./ctx doctor --store tests/fixtures/store-v1
./ctx help errors
```

No install step: `ctx` runs from a plain copy of the repo on Python ≥ 3.10. The interface (verbs, exit
codes, error strings, `--json` envelope, environment) is [`docs/interface.md`](docs/interface.md);
`ctx help` prints from the same file. Tests: `make ci`.

## Hooks

Every call a harness hook needs is one line. A write names its store; a read may find it by walking up.

| When | Call |
|---|---|
| Setup on a new machine, or a schema upgrade | `ctx init --store <path> --settings <file> --types <folder>` (add `--replace` to upgrade) |
| After a change under the store | `ctx validate --changed` (add `--adopt` while writes still come from outside ctx) |
| Heartbeat | `ctx touch --session <id>` |
| Session start | `ctx brief --registry` |
| After a compaction | `ctx brief --session <id>` |
| A step landed | `ctx log <doc> "<what happened>"` |
| Session end, or on a timer | `ctx maintain` |
| CI, after a schema change | `ctx migrate --check` |

## Claude Desktop

```json
{"mcpServers": {"ctx": {"command": "/path/to/ctx-store/ctx", "args": ["mcp"],
  "env": {"CTX_STORE": "/path/to/store", "CTX_ACTOR": "desktop"}}}}
```

On Windows with the store and `ctx` inside WSL, Claude Desktop starts it through `wsl.exe`, and the
environment goes into the arguments:

```json
{"mcpServers": {"ctx": {"command": "wsl.exe",
  "args": ["-e", "env", "CTX_STORE=/home/you/store", "CTX_ACTOR=desktop", "CTX_NO_WALK=1",
           "/home/you/ctx-store/ctx", "mcp"]}}}
```

| Install | `claude_desktop_config.json` is in |
|---|---|
| Windows, installer | `%APPDATA%\Claude\` |
| Windows, Microsoft Store | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\` |
| macOS | `~/Library/Application Support/Claude/` |

Tested with the Microsoft Store install on Windows with WSL2: the tools are listed, and `brief`, `find`
and `log` work. The other two rows are where Claude Desktop documents its config; they were not tested
here.

Every built verb is a tool (`ctx_brief`, `ctx_find`, `ctx_log`, …). `ctx memory` takes the input of
Anthropic's memory tool on stdin.

## claude.ai

`ctx-serve` puts the same tools behind HTTP with authentication (OAuth for claude.ai, a bearer token for
Claude Code), for a store on your machine behind a tunnel: [`docs/connector.md`](docs/connector.md). It is
a separate package; the core has no network.

## Speed

Milliseconds per call as a caller sees it: a fresh process each time, interpreter start included
(about 40 ms of every number). Generated Markdown stores of 3.1 MB and 39.2 MB, 20 runs per call,
Python 3.14 on Linux under WSL2. The two stores were measured in separate runs on a machine that was
not idle: compare a row with the first row of its own column. `python3 bench/bench.py` reproduces it.

| Call | 225 docs p50 | p95 | 3 000 docs p50 | p95 |
|---|---:|---:|---:|---:|
| start of the interpreter (`--version`) | 35 | 38 | 34 | 37 |
| `brief <doc>` | 35 | 40 | 34 | 39 |
| `get <doc> --section --tail 5` | 35 | 37 | 35 | 36 |
| `view <doc>` | 34 | 35 | 35 | 39 |
| `brief --registry` | 42 | 44 | 127 | 140 |
| `brief --session <id>` | 42 | 50 | 127 | 134 |
| `resolve <key>` | 41 | 46 | 103 | 107 |
| `find <word in one doc>` | 44 | 45 | 155 | 163 |
| `find <common word>` | 69 | 77 | 173 | 199 |
| `find --tag` | 41 | 59 | 123 | 144 |
| `validate` | 54 | 60 | 295 | 309 |
| `validate --changed` | 49 | 55 | 184 | 194 |

**No search cache.** The rule was: a cache enters only above 3 000 docs, with `find` over 200 ms at
p95, or when a lookup needs ranked search a scan cannot give. At 3 000 docs every `find` stays under
200 ms. `find` matches terms and ranks its hits as part of the same scan (`ctx help find`), which
finds 17 of the 18 lookups in `bench/eval/` where the phrase match before it found 6; the one it
misses is a paraphrase, which an index of words would miss too. A task described in a sentence
or two finds its doc as well (7 of 7 in the eval). That is where the scan costs most: about 0.1 s for
25 words on 225 docs, about 0.9 s on 3 000. So the store has no index to build,
validate or lose. The question returns when a store passes 3 000 docs, or when lookups need
synonyms.

`validate --changed`, the call behind the after-write hook, stays under its 300 ms budget at 3 000 docs.

**Status:** P4: every verb except `row` is built; measured; no cache. Design and phasing:
[#1](https://github.com/MdaaaaO/ctx-store/issues/1). Licence: MIT.
