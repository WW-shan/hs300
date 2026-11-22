# R495 Admitted Five-Factor Tail Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Confirm the strictly admitted five-factor set with bootstrap, HS300 excess and recent-tail gates, using the R419 execution configuration and no unadmitted factors.

**Architecture:** Append-only confirmation. Freeze the five R418-admitted factors, equal weights, 10 positions, 40-day hold and cap=1; run train/validation/test/recent plus 3x/5x and block bootstrap.

## Constraints

- Only the five R418-admitted factors.
- No leverage; equal weights.
- R419 execution configuration stays fixed.
- Test/recent confirmation only.
- 5x warning-only.

## Tasks

1. Freeze members/gates and add failing tests.
2. Build the admitted composite and run train/validation.
3. Confirm test/recent/3x/5x and bootstrap.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Exactly the five strictly admitted factors.
- No unadmitted factor in weights.
- Weights sum to 1 and exposure <=1.
- Final status follows all gates.
