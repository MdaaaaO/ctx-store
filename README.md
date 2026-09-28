# ctx-store

`ctx` is a markdown context store for coding agents: one folder of `*.md` rows with YAML frontmatter as
the schema, a CLI with validated structured writes (`log`, `fm`, `row`, `new`, `move`), budgeted reads
(`brief`, `find --budget`, `resolve`, `get --section --tail`), a per-actor audit trail and a maintenance
pass (`validate`, `doctor`, `maintain`, `migrate`). The same core serves three front-ends: the CLI (Claude
Code hooks and skills call it), an Anthropic memory-tool handler (`view create str_replace insert delete
rename`) and an MCP server (stdio for Claude Desktop; HTTP later).

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

Every built verb is a tool (`ctx_brief`, `ctx_find`, `ctx_log`, …). `ctx memory` takes the input of
Anthropic's memory tool on stdin.

## Speed

Milliseconds per call as a caller sees it: a fresh process each time, interpreter start included
(about 40 ms of every number). Generated Markdown stores of 3.1 MB and 39.2 MB, 20 runs per call,
Python 3.14 on Linux under WSL2. The two stores were measured in separate runs on a machine that was
not idle: compare a row with the first row of its own column. `python3 bench/bench.py` reproduces it.

| Call | 225 docs p50 | p95 | 3 000 docs p50 | p95 |
|---|---:|---:|---:|---:|
| start of the interpreter (`--version`) | 51 | 68 | 42 | 47 |
| `brief <doc>` | 54 | 92 | 47 | 50 |
| `get <doc> --section --tail 5` | 57 | 76 | 44 | 50 |
| `view <doc>` | 50 | 56 | 43 | 53 |
| `brief --registry` | 62 | 75 | 162 | 175 |
| `brief --session <id>` | 63 | 72 | 154 | 164 |
| `resolve <key>` | 57 | 71 | 126 | 157 |
| `find <word in one doc>` | 53 | 57 | 149 | 164 |
| `find <common word>` | 57 | 65 | 153 | 168 |
| `find --tag` | 60 | 79 | 169 | 188 |
| `validate` | 76 | 90 | 370 | 411 |
| `validate --changed` | 62 | 72 | 239 | 285 |

**No search cache.** The rule was: a cache enters only above 3 000 docs, with `find` over 200 ms at
p95, or when a lookup needs ranked search a scan cannot give. At 3 000 docs every `find` stays under
200 ms and no caller needs ranking, so the store has no index to build, validate or lose. The
question returns when a store passes 3 000 docs.

`validate --changed`, the call behind the after-write hook, stays under its 300 ms budget at 3 000 docs.

**Status:** P4: every verb except `row` is built; measured; no cache. Design and phasing:
[#1](https://github.com/MdaaaaO/ctx-store/issues/1). Licence: MIT.
