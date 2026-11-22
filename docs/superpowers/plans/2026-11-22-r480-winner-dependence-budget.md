# R480 Winner-Dependence Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether replacing the R476 9-leg winner-dependent structure with a pre-registered lower-dependence factor subset improves out-of-sample and recent robustness without leverage.

**Architecture:** Append-only controlled comparison. Build a fixed small set of 8-leg subsets from the frozen R476 library, measure train+validation winner-dependence and return only there, select by train+validation, then confirm once on test/recent. Use `000300.SH` for excess return.

## Constraints

- No leverage; 2x main, 3x stress, 5x warning-only.
- Subsets, budget, gates and windows are frozen before running.
- Test/recent cannot change the subset or parameters.
- Current HS300 membership/industry snapshot limitations remain explicit.
- No orders or production activation.

## Tasks

1. Freeze subset construction, dependence metric and gates; write failing tests.
2. Rebuild the R476 9-leg panel and materialize each subset composite.
3. Run train+validation selection with trade-level dependence metrics.
4. Run one-shot test/recent/stress confirmation and HS300-relative diagnostics.
5. Verify, commit and push.

## Acceptance

- Every subset has exactly 8 legs, weights sum to 1 and no leverage.
- Selection uses train+validation only.
- Final strategy status is `watch_candidate` only if all confirmation gates pass; otherwise `rejected`.
- 5x cannot hard-reject.
- JSON/Markdown sidecars verify.
