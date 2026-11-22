# R477 Frozen Factor-Library Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Diagnose why the R476 expanded factor library passes train/validation but loses most of its edge in test/recent, using the already frozen R476 sample and no new parameter selection.

**Architecture:** Append-only R477 runner reloads the R476 sample weights, directions and execution parameters, reruns only the confirmation windows with full equity, benchmark and trade records, and writes deterministic diagnostics. No test/recent metric selects or changes any parameter.

## Constraints

- Reuse the exact R476 selected sample; do not search a new grid.
- 2x main cost, 3x stress, 5x warning-only; no leverage.
- Report yearly, monthly, rolling one-year, benchmark-relative, trade concentration and symbol concentration.
- Do not overwrite R476 or create orders/production configuration.
- JSON, Markdown and preregistration need SHA-256 sidecars.

## Tasks

1. Freeze the R477 diagnostic schema and window set; add failing tests for the frozen sample, no-leverage and cost separation.
2. Rebuild the R476 9-leg panel and rerun the frozen sample over test and recent windows with full result materialization.
3. Compute diagnostics and write JSON/Markdown/sidecar.
4. Run targeted/full verification, commit and push.

## Acceptance

- Frozen sample has 9 legs, weights sum to 1 and includes the R476 added factor.
- Test/recent are not used to alter any parameter.
- 2x/3x/5x results are distinct.
- Diagnostics are reproducible from the checked-in R476 sample.
