# R488 Drawdown-Conditional Defensive Asset Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether bond/gold ETFs can offset R484’s long drawdown cluster when used only under pre-registered defensive conditions, rather than a fixed all-weather blend.

**Architecture:** Append-only conditional mix audit. Keep R484 equity sleeve frozen and use T-1 signals to switch between cash, bond ETF and gold ETF. Select only on train+validation; test/recent are confirmation.

## Constraints

- No leverage; equity + defensive + cash <= 1.0.
- Defensive weights use only T-1 signals.
- Equity sleeve, cap and parameters frozen from R484.
- 5x remains warning-only.
- ETF history is local ignored data; fail closed if missing.

## Defensive ETFS

- `511010.SH` 国债ETF
- `518880.SH` 黄金ETF

## Pre-registered defensive rules

- baseline: 100% R484 equity.
- `cash20_always`: 80% equity / 20% cash.
- `bond20_always`: 80% equity / 20% bond.
- `bondgold20_always`: 80% equity / 10% bond / 10% gold.
- `drawdown_bond20`: if R484 T-1 drawdown <= -5%, 70% equity / 30% bond; else 100% equity.
- `drawdown_bondgold30`: if R484 T-1 drawdown <= -5%, 70% equity / 15% bond / 15% gold; else 100% equity.
- `weak_bondgold30`: if T-1 regime state in {weak, lean_weak}, 70% equity / 15% bond / 15% gold; else 100% equity.

## Tasks

1. Freeze rules/gates; add tests for T-1 and bounded weights.
2. Recover R484 daily curves and ETF daily curves.
3. Run train+validation selection and test/recent confirmation.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- All weights sum <= 1 and no leverage.
- Conditional rules use T-1 only.
- Final status follows all gates.
