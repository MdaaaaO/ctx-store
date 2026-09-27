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

**Status:** bootstrapping (P0). Design and phasing:
[#1](https://github.com/MdaaaaO/ctx-store/issues/1). Licence: Apache-2.0.
