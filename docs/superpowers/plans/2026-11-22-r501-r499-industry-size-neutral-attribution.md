# R501 R499 Industry/Size-Neutral Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Determine how much of each frozen R499 leg's signal survives industry+size neutralization across train, validation, test and recent, and whether the recent losing-month problem is a stock-selection failure or an industry/size exposure failure.

**Architecture:** Append-only neutralization audit. Reuse the R499/R463 leg definitions and R500's losing-month data. Neutralize each leg by current industry L1 and log float market cap, compute rank IC by window, and compare raw versus neutralized directional IC and sign stability. No portfolio reselection, no weight change, no promotion.

## Constraints

- Legs and directions are exactly R499.
- Neutralization is `industry_l1` plus `log_float_mv`.
- No factor formula changes and no portfolio parameters.
- Test/recent are confirmation diagnostics only.
- 5x remains warning-only.

## Tasks

1. Freeze neutralization spec and audit windows; add failing tests.
2. Reconstruct the R499 panel and leg scores.
3. Compute raw and neutralized rank IC for every leg/window.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
