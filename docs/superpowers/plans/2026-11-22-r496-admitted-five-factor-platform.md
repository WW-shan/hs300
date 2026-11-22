# R496 Admitted Five-Factor Parameter Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Determine whether R495’s strictly admitted five-factor set has a stable parameter platform rather than a single lucky parameter point.

**Architecture:** Append-only platform audit. Freeze the five factors; vary positions, hold, entry/exit thresholds and cap. Select only on train+validation, check neighborhood stability, then test/recent confirmation.

## Constraints

- Only the five R418-admitted factors.
- No leverage; equal weights.
- Cap values 1/2/3 and baseline unrestricted.
- Train+validation selection; test/recent confirmation.
- 5x warning-only.

## Grid

- positions: 10/20
- hold: 30/40/50
- entry/exit: 60/70/80 × 30/40/50
- cap: None/1/2/3
- total: 216 samples

## Tasks

1. Freeze grid/gates; add failing tests.
2. Build admitted composite.
3. Run train+validation; rank and compute local platform.
4. Confirm selected on test/recent/3x/5x and bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.

## Acceptance

- Every sample has exactly the five admitted factors.
- Weights normalized and exposure <=1.
- Selection uses train+validation only.
- Platform audit is reported.
