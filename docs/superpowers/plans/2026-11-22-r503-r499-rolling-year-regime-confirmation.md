# R503 R499 Rolling-Year and Market-Segment Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Determine whether the frozen R499 weak-state tilt is structurally positive across rolling one-year starts and calendar/regime segments, rather than only on the two confirmation windows.

**Architecture:** Append-only frozen-sample audit. Recompute R499 once per rolling start with identical weights, threshold and execution; report one-year absolute and HS300-relative outcomes, calendar-year outcomes, regime-segment outcomes, and block-bootstrap sensitivity. No reselection.

## Constraints

- Freeze R499 weights, weak threshold and execution exactly.
- Rolling starts are pre-registered and do not enter selection.
- No factor/weight/parameter search.
- 2x main cost, 3x stress on a fixed subset of starts, 5x not used as a pass gate.
- No leverage.

## Tasks

1. Freeze rolling starts, calendar and regime segment definitions; add failing tests.
2. Recompute the frozen strategy for each rolling start.
3. Aggregate rolling-year, calendar-year and regime-segment outcomes.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
