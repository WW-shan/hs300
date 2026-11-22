# R518 Macro Coverage Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Classify local macro and flow variables as full-history, partial-history or unusable for R506 conditional research.

**Architecture:** Read-only coverage audit. Measure distinct dates, first/last dates and recent availability for each variable; no backtest or rule selection.

## Constraints

- Local data only.
- No network fetch.
- No strategy promotion.
- Current HS300 membership remains a known bias.

## Tasks

1. Freeze coverage gates; add failing tests.
2. Audit each conditioner.
3. Write JSON/Markdown/sidecar.
4. Verify, commit and push.
