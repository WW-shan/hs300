# R517 Industry-History Feasibility Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Determine whether the local ETF/index history is sufficient for a genuine 2018-start industry-rotation research program.

**Architecture:** Read-only feasibility audit. Inventory ETF and index instruments, compute history length and coverage, and classify industry proxies as usable, too short, or non-existent.

## Constraints

- No backtest and no parameter search.
- No network fetch in this round.
- No leverage and no production activation.
- Current industry mapping is not treated as historical industry history.

## Tasks

1. Freeze feasibility gates; add failing tests.
2. Inventory ETF/index instruments and daily coverage.
3. Classify industry proxies.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
