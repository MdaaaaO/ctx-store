<!--
Title = the squash commit on main. Conventional Commits, lower-case subject, no trailing period,
≤ 72 characters:

  feat(core): add the locked log append
  fix(cli): reject an unknown option after the verb
  docs(interface): name the detail slot of every error
  ci(repo): pin the actions by commit

Types: feat fix docs chore refactor test ci build perf style revert
-->

Closes #
<!-- Every PR comes from an issue. "Closes #N" = this PR finishes it (GitHub closes it on merge).
     "Refs #N" = one step of a larger issue, which stays open. No issue yet? Create it first.
     The pr-issue check fails without one. -->

## What

## Why

## Contract

<!-- Does the interface change (verbs, options, exit codes, error strings, the --json envelope, environment,
     store layout)? "No", or what changes and whether `api` moves. A removed or re-meant field is `breaking`. -->

## Verified how

- [ ] `make ci` green locally
- [ ] `ctxstore/interface.md` says what the code does; goldens regenerated with `make golden` and the diff read
- [ ] standard library only, no network, the filesystem only through `ctxstore/fs.py`
