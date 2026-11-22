# R498 Drawdown-Conditional Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Explain whether the R463 five-leg portfolio's persistent drawdown cluster has a stable, T-1 observable factor condition, and which legs contribute positively or negatively inside it.

**Architecture:** Append-only diagnostic. Reconstruct the frozen R463 portfolio and curve, derive weak windows from train/validation only, then compute leg-level conditional IC and return contribution inside those windows. Test/recent are confirmation-only diagnostics and never select a rule.

## Constraints

- Base portfolio is the frozen R463 five-leg set: four admitted legs + 10% `value_quality`.
- Execution is frozen: 10 positions, 25-day hold, 2x main cost, no leverage.
- Weak-state thresholds are estimated only from train+validation, then applied unchanged to test/recent.
- No new factor formulas; no portfolio promotion.
- 5x is warning-only and never a hard rejection.

## Tasks

1. Freeze the portfolio, windows and conditional diagnostics; add failing tests.
2. Reconstruct the frozen portfolio and train/validation curves.
3. Compute T-1 weak-state labels and leg-level conditional contribution.
4. Confirm the frozen label on test/recent and write JSON/Markdown/sidecar.
5. Verify, commit and push.
