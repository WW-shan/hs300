# R505 R499 Daily Entry-Cap Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether limiting daily new-position batches reduces the frozen R499 portfolio's dependence on a few trades and improves recent tails without changing factor weights.

**Architecture:** Append-only execution audit. Freeze R499 factors, weights, weak-state tilt, 10 positions and 25-day hold. Vary only `max_new_positions_per_day` over none/1/2/3, select on train+validation, confirm test/recent/stress.

## Constraints

- Factors, weights, threshold and execution prices stay fixed.
- Only daily new-position cap changes.
- Selection only on train+validation.
- No leverage; total exposure <=1.
- 5x warning-only.

## Tasks

1. Freeze cap grid and gates; add failing tests.
2. Reconstruct R499 panel and run train+validation for each cap.
3. Select by worst-window robustness, then lower concentration.
4. Confirm selected cap on test/recent/3x/5x and paired bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
