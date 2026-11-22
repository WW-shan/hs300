# R482 R480 Volatility-Scaling Risk Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether a no-leverage daily volatility-scaling overlay on the R480 eight-leg subset can reduce bootstrap tail risk while preserving test/recent returns and HS300 excess.

**Architecture:** Append-only overlay audit. Reuse the frozen R480 subset and its daily equity curve; construct pre-registered cash/equity scaling rules from only past returns, then recompute confirmation metrics. No factor or selection changes.

## Constraints

- No leverage: portfolio weight `<= 1.0`.
- Volatility estimates use trailing data only.
- R480 subset, weights, entry/exit and holding parameters stay frozen.
- 5x remains warning-only.
- R480 remains rejected unless the overlay passes every gate on its own merit.

## Pre-registered overlays

- Baseline: weight 1.0.
- `vol_target_12`: target 12% annualized, 60-day lookback, weight 0.2..1.0.
- `vol_target_15`: target 15% annualized, 60-day lookback, weight 0.2..1.0.
- `vol_target_18`: target 18% annualized, 60-day lookback, weight 0.2..1.0.
- `drawdown_guard`: if trailing drawdown from prior peak <= -10%, weight 0.5; otherwise 1.0.

## Tasks

1. Freeze overlays and gates; write failing tests.
2. Load R480 frozen sample and confirmation curves.
3. Compute overlay weights using trailing data only and apply to daily returns.
4. Bootstrap and HS300 comparison; write append-only evidence.
5. Verify, commit and push.

## Acceptance

- All weights in `[0,1]`.
- Overlay uses no future returns.
- Baseline curve is reproduced from the frozen R480 result.
- Final status stays rejected unless every gate passes.
