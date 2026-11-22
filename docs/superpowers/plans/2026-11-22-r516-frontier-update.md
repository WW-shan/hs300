# R516 Frontier Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Consolidate R511-R515 into the factor-library frontier so growth, industry conditioning and dynamic risk budget are not re-tested blindly.

**Architecture:** Append-only summary of existing evidence. No new backtest, no factor changes, no promotion.

## Constraints

- Reuse existing R510-R515 evidence.
- No new data or parameter search.
- No leverage and no production activation.

## Tasks

1. Freeze source hashes; add failing tests.
2. Read R510-R515 evidence.
3. Write JSON/Markdown/sidecar.
4. Verify, commit and push.
