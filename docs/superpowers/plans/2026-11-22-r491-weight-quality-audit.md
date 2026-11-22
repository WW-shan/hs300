# R491 R490 Weight-Quality Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether the R490 five-leg result survives alternative pre-registered weightings derived from train+validation evidence, instead of relying on equal weights.

**Architecture:** Append-only weighting audit. Use only train+validation rank IC and stability to derive fixed weights; apply those weights unchanged to test/recent.

## Constraints

- R490 five legs, directions, cap=1 and execution frozen.
- No test/recent-derived weights.
- Weights nonnegative, sum=1, no leverage.
- Compare equal, IC-weighted, stability-weighted and equal-risk approximations.
- 5x warning-only.

## Tasks

1. Freeze weighting rules and gates; add tests.
2. Build R490 panel and estimate train/validation ICs.
3. Run fixed-weight variants train+validation; select by train+validation.
4. Confirm selected on test/recent/3x/5x.
5. Write JSON/Markdown/sidecar; verify, commit and push.

## Acceptance

- Weights nonnegative and sum to 1.
- No test/recent enters weight calculation.
- Final status follows all gates.
