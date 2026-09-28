---
title: Alert patterns
type: reference
tags: [oncall, alerts]
updated: 2026-01-10
---

# Alert patterns

## orders_daily / load_orders
Known. The task timed out after 3600 s waiting for the warehouse to resume.
Rerun once the warehouse is up; no data is lost.

### Recurrence log
- 2026-01-04 — timed out, rerun green.
- 2026-01-09 — timed out, rerun green.

## orders_daily_v2 / load_orders
Known. Fails when the upstream export is late: the source file is missing at 02:00.
Wait for the export, then clear the task.

### Recurrence log
- 2026-01-06 — export late by 40 minutes.

## customers_snapshot / dedupe
Known. Duplicate keys after a replay of the change stream. Run the dedupe job by hand.

### Recurrence log
- 2026-01-02 — replay after an outage.
