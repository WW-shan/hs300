# R486 Drawdown-Cluster Tail Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Determine whether R484’s negative bootstrap tail is driven by clustered multi-day drawdowns or isolated crash days, so the next risk control targets the right mechanism.

**Architecture:** Append-only diagnostic on the selected R484 curve. Recompute daily returns, drawdown episodes, rolling 20-day windows and block-bootstrap cluster contributions; do not change strategy parameters.

## Constraints

- No reselection and no test/recent parameter tuning.
- Report both daily and clustered drawdown metrics.
- R484 remains rejected.
- No leverage/orders/production.

## Tasks

1. Freeze diagnostic schema and thresholds.
2. Load/recompute selected R484 test/recent curves.
3. Compute drawdown episodes, worst rolling windows and return contribution by exposure.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Drawdown episodes are non-overlapping and dated.
- Both test/recent are reported.
- Output is diagnostic-only.
