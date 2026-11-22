# R478 Factor-Library Bootstrap Tolerance Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate “essentially flat bootstrap tail” from “real negative tail” for the R476 expanded library without loosening any primary return, Sharpe, drawdown or recent-window gate.

**Architecture:** Append-only audit of the already-frozen R476 sample distributions. A tolerance band of 1 percentage point is applied only to the validation/bootstrap tail diagnostic; all original R476 gates remain unchanged and the final decision cannot become watch-ready from this audit.

## Constraints

- Do not rerun or reselect R476 samples.
- Do not use test/recent to choose weights or parameters.
- `bootstrap_near_zero_tolerance = 0.01`; `p05 >= -0.01` is near-zero, not positive.
- Original R476 hard gate remains the authority for promotion.
- No leverage, no orders, no production configuration.

## Tasks

1. Freeze the audit schema and tolerance; add failing tests.
2. Recompute summary classifications for all eligible R476 samples.
3. Write JSON/Markdown/sidecar, including a clear “not promoted” decision.
4. Run targeted/full verification, commit and push.

## Acceptance

- Every eligible sample has one of `positive`, `near_zero`, `negative` bootstrap tail.
- Near-zero is never described as positive or passing.
- R476 remains rejected; `active_version` stays null.
- JSON/Markdown checksums verify.
