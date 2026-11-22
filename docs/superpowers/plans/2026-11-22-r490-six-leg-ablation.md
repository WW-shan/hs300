# R490 Six-Leg Ablation and Marginal Contribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Identify which legs of the R489 six-leg subset carry its return and which are redundant, using leave-one-out 5-leg portfolios.

**Architecture:** Append-only ablation. Freeze R489 six-leg weights, cap=1 and execution; remove exactly one leg at a time. Select on train+validation, confirm test/recent.

## Constraints

- No leverage; all weights normalized.
- Only one leg removed per sample; no new factors.
- Cap=1 and execution frozen.
- Test/recent confirmation only.
- 5x warning-only.

## Tasks

1. Freeze ablation and gates; add tests.
2. Build six R489 legs and run 5-leg leave-one-out train/validation.
3. Run selected ablation on test/recent/stress/bootstrap.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Six 5-leg samples, each missing exactly one R489 leg.
- Weights sum to 1 and exposure <=1.
- Final status follows all gates.
