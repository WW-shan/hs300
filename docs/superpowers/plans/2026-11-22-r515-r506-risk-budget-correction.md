# R515 R506 Risk-Budget Confirmation Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Re-run the R514 dynamic risk-budget confirmation with all selected overlays, including baseline, evaluated on test/recent/3x so the gate never reads missing fields.

**Architecture:** Append-only correction. Freeze R514's overlay definitions and selection rule; recompute confirmation for every selected variant.

## Constraints

- Same overlays, windows and gates as R514.
- No new overlays or weight changes.
- No leverage; 5x warning-only.

## Tasks

1. Freeze source and correction; add failing tests.
2. Recompute R514 candidate selection.
3. Evaluate selected variant on all confirmation windows.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
