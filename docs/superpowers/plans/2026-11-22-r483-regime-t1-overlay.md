# R483 T-1 Regime Overlay on R480 Eight-Leg Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether T-1 market state/phase/score gates improve the R480 eight-leg sleeve's test/recent tail without using same-day regime data.

**Architecture:** Append-only interaction audit. Recover the frozen R480 daily curves, join pre-open T-1 regime rows, apply pre-registered cash/equity gates, select only on train+validation, then confirm once on test/recent.

## Constraints

- Regime row used for day T is strictly date T-1.
- Only daily exposure is changed; factor selection and R480 parameters stay frozen.
- No leverage (`weight <= 1.0`).
- 5x remains warning-only.
- R480 remains rejected unless the overlay passes every gate.

## Pre-registered overlays

- baseline: always 1.0.
- weak_off: 0 on `state=weak`, otherwise 1.
- weak_leanweak_off: 0 on `weak/lean_weak`, otherwise 1.
- score_lt_25_off: 0 if T-1 score < 25, otherwise 1.
- score_lt_40_half: 0.5 if T-1 score < 40, otherwise 1.
- strong_only: 1 only on T-1 `state in {strong, lean_strong}`.
- phase_ebb_off: 0 on T-1 `phase=ebb`, otherwise 1.

## Tasks

1. Freeze overlays/gates; add failing tests.
2. Recover frozen R480 curves and T-1 regime mapping.
3. Run train+validation selection and one-shot confirmation.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.

## Acceptance

- Regime lookup is strictly shifted by one date.
- All overlay weights are in `[0,1]`.
- Test/recent cannot alter selection.
- Final status follows all gates.
