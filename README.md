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

**Status:** P3: every verb except `row` is built. Design and phasing:
[#1](https://github.com/MdaaaaO/ctx-store/issues/1). Licence: MIT.
