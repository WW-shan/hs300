# R484 Daily Entry-Batch Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether limiting new positions per signal day reduces R480’s batch/month dependence and bootstrap tail without destroying HS300 excess.

**Architecture:** Core matcher gets an opt-in `max_new_positions_per_day`; default `None` preserves all existing behavior. R484 freezes the R480 eight-leg sleeve and compares caps 1/2/3 versus baseline using train+validation selection and one-shot test/recent confirmation.

## Constraints

- No leverage; max exposure <= 1.0.
- Factor members, weights, entry/exit, hold and top_rank are frozen.
- Cap is applied during execution, not by deleting trades post hoc.
- Test/recent cannot select the cap.
- 5x remains warning-only.

## Tasks

1. Add failing matcher test for same-day cap; implement opt-in config.
2. Freeze cap audit and gates; add runner tests.
3. Run train+validation caps, then test/recent/stress confirmation.
4. Write JSON/Markdown/sidecar.
5. Run full verification, commit and push.

## Acceptance

- Baseline cap `None` reproduces prior behavior.
- Cap `k` limits new positions per day without changing other semantics.
- Final status follows all gates; no orders/production.
