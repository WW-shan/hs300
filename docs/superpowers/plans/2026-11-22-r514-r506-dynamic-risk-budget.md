# R514 R506 Dynamic Risk-Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether trailing-return-only volatility targeting or drawdown guards improve the frozen R506 cap-1 sleeve's recent tail, without changing stock selection.

**Architecture:** Append-only overlay audit. Recompute R506 cap-1 curves, apply no-leverage cash/equity scaling using only past returns, select on train+validation, and confirm test/recent/3x.

## Constraints

- Base is exactly R506 cap-1.
- No factor or weight changes.
- Exposure is always within [0,1].
- Only trailing data is used for scaling.
- 2x main, 3x stress, 5x warning-only.

## Tasks

1. Freeze overlays and gates; add failing tests.
2. Recompute R506 curves.
3. Apply volatility-target and drawdown-guard variants.
4. Confirm selected overlay on test/recent/3x and bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
