---
title: Retry failed payments
type: epic
status: active
tags: [payments]
updated: 2026-02-03
---

# Retry failed payments

## Tracker & links
- Tracking issue: EX-202

## Goal
A failed payment is retried three times before the order is cancelled.

## Related
- EX-101 changes the checkout the retry hooks into.
- EX-101 region two uses the old payment provider.
- EX-101 region three is blocked on this work.
- See EX-101 for the rollout order.

## Session log
- 2026-01-20 — created.
- 2026-02-03 — provider sandbox works.
