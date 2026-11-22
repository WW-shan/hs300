# R485 Cap × Hold × Exposure Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether the R484 daily-entry cap combined with a small hold/exposure grid improves bootstrap tail while retaining test/recent HS300 excess.

**Architecture:** Append-only interaction grid. Freeze R484 factor weights, scores, top_rank and cap=1; vary only max hold days and total exposure. Select on train+validation; confirm once on test/recent.

## Constraints

- No leverage (`max_exposure_pct <= 1.0`).
- Only `max_hold_days` and `max_exposure_pct` vary.
- R484 factor members, weights, scores and cap remain fixed.
- Test/recent cannot influence selection.
- 5x remains warning-only.

## Grid

- hold: 20/30/40/50
- exposure: 0.8/0.9/1.0
- total: 12 samples

## Tasks

1. Freeze grid/gates; add failing tests.
2. Rebuild R484 frozen composite and run train+validation grid.
3. Run one-shot test/recent/stress confirmation.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Every sample has same weights, directions, cap and scores.
- All exposures <= 1.
- Final status follows all gates.
