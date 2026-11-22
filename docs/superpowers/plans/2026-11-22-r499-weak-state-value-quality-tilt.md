# R499 Weak-State Value-Quality Tilt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test the single hypothesis isolated by R498: in T-1-defined weak markets, reduce the frozen R463 portfolio's `value_quality` weight and reallocate it to the four consistently positive admitted legs, then confirm whether the drawdown tail improves without relying on the test window for selection.

**Architecture:** Append-only conditional-weight backtest. Freeze the R463 members and execution. Use only train+validation metrics to choose among a small pre-registered weak-state tilt grid. Recompute the per-date weighted composite inside the same T+1 portfolio engine. Test/recent are confirmation only.

## Constraints

- Base members and directions are exactly R463.
- No leverage; total weight is always 1.
- Weak-state threshold is exactly R498's train+validation-derived threshold.
- Only weak-state `value_quality` weight changes; non-weak weights stay at R463 values.
- Candidate weak weights: 0%, 2.5%, 5%; the remaining weight is split equally across the four admitted legs.
- No factor formula changes, no position/hold/threshold search.
- Test/recent are confirmation only; 2x main cost, 3x stress, 5x warning-only.

## Tasks

1. Freeze the candidate grid and conditional weights; add failing tests.
2. Reconstruct R463 panel and train/validation curves.
3. Run train+validation for all three candidates; select by worst-window robustness, then lower test-independent risk.
4. Confirm the selected candidate on test/recent/3x/5x and paired HS300 bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
