# R502 Industry/Size-Neutral Leg Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether replacing the two R499 legs whose industry+size-neutral contribution is weakest (`fund_holding_ratio_low`, `cash_flow_stability_4q_low`) with stronger neutralized alternatives improves the frozen R499 portfolio without using test/recent for selection.

**Architecture:** Append-only replacement audit. Keep R499's weak-state value-quality tilt, execution and all other legs fixed. Pre-register at most one replacement per problematic leg from an existing research-backed candidate, with equal-weight renormalization. Select on train+validation only; test/recent are confirmation.

## Constraints

- Base portfolio and weak threshold are exactly R499.
- Only `fund_holding_ratio_low` and/or `cash_flow_stability_4q_low` may be replaced.
- At most one candidate per replacement family; no combinatorial factor search.
- Candidate must already have a clear research record and no new formula.
- No leverage; total weights sum to 1.
- 2x main, 3x stress, 5x warning-only.

## Candidate families

- For fund-holding risk: `ext_alt_hs300_fund_count_low` is not preferred because its recent IC is negative; prefer no replacement unless a neutralized candidate is positive across train/validation/test/recent.
- For cash-flow stability: `ext_alt_hs300_ocf_margin_4q` is already research-evidenced and has a positive recent IC but lacks incremental admission; use only as a diagnostic replacement, not as an admitted factor.
- If neither passes, record the null replacement result.

## Tasks

1. Freeze candidate list and validation gates; add failing tests.
2. Reconstruct R499 panel and weak-state dates.
3. Run base plus each pre-registered replacement on train+validation.
4. Confirm selected candidate on test/recent/3x/5x and paired bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
