# R492 Paired HS300 Excess Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate R490’s absolute tail risk from its active/excess tail risk using date-aligned paired moving-block bootstrap against `000300.SH`.

**Architecture:** Append-only diagnostic on frozen R490 curves. Pair strategy and HS300 daily returns by exact date, resample identical blocks, and report absolute, benchmark and excess distributions. No strategy reselection.

## Constraints

- R490 five legs, cap=1 and execution frozen.
- Pair dates exactly; no forward fill or nearest-day substitution.
- Report absolute and excess tails side by side.
- No leverage and no promotion.

## Tasks

1. Freeze paired bootstrap protocol and output schema.
2. Recover R490 test/recent curves and exact-date HS300 returns.
3. Compute absolute and active paired block bootstrap.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Pair counts/date ranges are reported.
- Missing benchmark days fail closed.
- R490 remains rejected.
- Output is diagnostic-only.
