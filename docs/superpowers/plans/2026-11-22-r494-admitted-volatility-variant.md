# R494 Admitted Volatility Variant Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace R490’s non-R418-admitted `vol_of_vol_20d_low` leg with the strictly admitted `vol_of_vol_60d_low` and test whether the five-leg sleeve remains valid.

**Architecture:** Append-only variant confirmation. Freeze R490’s other four legs, cap=1 and execution; change only the volatility-stability leg. Train+validation selection; test/recent confirmation.

## Constraints

- No leverage; equal weights, sum=1.
- Only `vol_of_vol_20d_low` -> `vol_of_vol_60d_low`.
- Test/recent confirmation only.
- 5x warning-only.
- This round must not rewrite R490’s evidence.

## Tasks

1. Freeze the replacement and gates; add failing tests.
2. Build the R490 core plus `vol_of_vol_60d_low`.
3. Run train/validation; confirm test/recent/stress/bootstrap.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Exactly one leg differs from R490.
- Weights normalized and exposure <=1.
- Final status follows all gates.
