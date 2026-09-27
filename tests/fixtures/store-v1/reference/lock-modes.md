---
title: Lock modes
type: reference
status: active
tags: [locks, filesystems]
updated: 2026-01-05
---

# Lock modes

## Summary
`flock` on local filesystems, the `mkdir` lock where flock cannot be proven.

## Details
A network mount may accept `flock` and not enforce it, so the mode is probed.
