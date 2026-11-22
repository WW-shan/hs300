# R493 Flow-Factor Augmentation of R490 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether adding a pre-registered flow/attention factor to the R490 five-leg sleeve improves tail and HS300 excess without relying on test/recent.

**Architecture:** Append-only augmentation audit. Add one flow factor at a fixed 10/20/30% weight to the frozen R490 five-leg core. Train+validation selection; test/recent confirmation.

## Constraints

- No leverage; weights normalized.
- Core five legs and cap=1/40-day execution frozen.
- Candidate flow factors must have full local coverage in the backtest window.
- Test/recent confirmation only.
- 5x warning-only.

## Candidates

- `ext_alt_hs300_lhb_net_buy_ratio_20d`
- `ext_alt_hs300_lhb_inst_net_buy_ratio_20d`
- `ext_alt_hs300_lhb_event_count_20d_low`

## Tasks

1. Freeze candidate/weight grid and gates; add tests.
2. Build R490 core panel and candidate flow columns.
3. Run train+validation augmentation grid; select.
4. Confirm selected on test/recent/3x/5x + bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.

## Acceptance

- Every sample has five core legs + one flow leg.
- Weights nonnegative, sum=1, no leverage.
- Test/recent not used for selection.
- Final status follows all gates.
