# R513 Industry-Momentum Conditioning Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether the frozen R506 factor sleeves work differently in strong versus weak industry-momentum states, before attempting any industry-rotation portfolio.

**Architecture:** Append-only conditional IC audit. Use existing `industry_mom_20d`, `industry_rps_20d` and `industry_breadth_20d` proxies, lag them one trading day, and compare neutral factor IC across expanding terciles. No portfolio backtest and no rule selection in this round.

## Constraints

- Reuse R506/R499 factor sleeves exactly.
- Conditioners are lagged one trading day.
- Expanding historical terciles only.
- No industry-cap or rotation strategy yet.
- No leverage and no production activation.

## Tasks

1. Freeze conditioner set, lag and gates; add failing tests.
2. Build R506 sleeve scores and lagged conditioners.
3. Compute conditional neutral IC by state.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
