# R509 Amihud Direction Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Correct the `amihud_20d` direction in R508 and re-evaluate only that candidate as a low-weight adjunct to the frozen R506 cap-1 base.

**Architecture:** Append-only correction. Preserve R508 unchanged. Reuse R508's base, weights and gates, changing only `amihud_20d` from low to high according to its registered liquidity definition.

## Constraints

- Only `amihud_20d` is evaluated.
- Added weights remain 5%/10%.
- Base and other execution parameters are exactly R508.
- Train+validation selection; test/recent confirmation.
- 5x warning-only; no leverage.

## Tasks

1. Freeze correction and source hashes; add failing tests.
2. Recompute base and two amihud variants.
3. Confirm any eligible variant on test/recent/3x/5x.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
