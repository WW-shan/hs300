# R507 Cap-1 Weak-State Exposure Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether a T-1 weak-state cash exposure gate can turn the frozen R506 cap-1 recent absolute tail positive without changing its factor weights.

**Architecture:** Append-only exposure overlay audit. Recompute the frozen R506 cap-1 curve, apply only weak-state exposure multipliers 50%/75%/100%, select on train+validation, confirm test/recent/3x. No factor/weight/parameter search.

## Constraints

- Base sample is exactly R506 `cap_1`.
- Only weak-state exposure changes.
- Weak-state definition is exactly R498/R499 threshold.
- Exposure never exceeds 1.0.
- No leverage; 5x remains warning-only.

## Tasks

1. Freeze exposure variants and gates; add failing tests.
2. Recompute R506 cap-1 curves.
3. Apply T-1 exposure overlays and select on train+validation.
4. Confirm test/recent/3x and paired bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
