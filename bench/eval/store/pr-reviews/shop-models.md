---
title: Review notes for shop-models
type: reference
tags: [pr-review, shop-models]
updated: 2026-01-11
---

# Review notes for shop-models

## Known traps to check on every PR
- A new column needs a test for nulls.
- Incremental models need a unique key.

## Order model
The daily orders model is incremental. Late-arriving orders change the totals of
earlier days, so the model reprocesses the last three days on every run.

## Customer table
Customers are deduplicated by e-mail address. The discount_amount column is filled
only for customers with a loyalty tier; everywhere else it is null, not zero.
