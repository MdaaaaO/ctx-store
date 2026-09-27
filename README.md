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

**Status:** bootstrapping (P0): `doctor` and `help` are built, the other verbs are specified. Design and phasing:
[#1](https://github.com/MdaaaaO/ctx-store/issues/1). Licence: MIT.
