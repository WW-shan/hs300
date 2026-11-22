# R487 Rebalance/Re-entry Horizon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether shorter re-evaluation/re-entry horizons reduce the long R484 drawdown cluster while preserving HS300 excess.

**Architecture:** Append-only centered horizon audit on the frozen R484 eight-leg subset and cap=1. Vary only maximum hold/re-entry horizon; no factor or weight changes.

## Constraints

- No leverage; exposure <= 1.0.
- `max_new_positions_per_day=1` stays fixed.
- Select on train+validation only; test/recent confirmation.
- 5x warning-only.

## Grid

- hold/re-entry horizon: 10/15/20/25/30/40/50
- positions: 10
- entry/exit/top_rank frozen from R484

## Tasks

1. Freeze grid/gates; add failing tests.
2. Rebuild R484 composite and run train+validation horizons.
3. Confirm selected horizon on test/recent/3x/5x.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- All samples have the same leg set and cap.
- Only hold horizon varies.
- Final status follows all gates.
