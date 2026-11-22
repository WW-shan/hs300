# R511 Growth Acceleration IC Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Audit two economically distinct growth candidates not covered by the rejected static growth levels: revenue-growth acceleration and quality-confirmed earnings growth.

**Architecture:** Append-only IC audit. Build only two composites from existing PIT financial fields, apply industry+size neutralization, and test 5/20/60-day rank IC with yearly and recent stability. No portfolio backtest unless a candidate passes all IC gates.

## Constraints

- No new raw data source.
- PIT attachment uses the project's statutory-deadline fallback.
- Only two pre-registered composites.
- No weight or portfolio search in this round.
- No leverage; no production activation.

## Tasks

1. Freeze composite formulas and IC gates; add failing tests.
2. Materialize existing financial fields with PIT attachment.
3. Compute raw/neutral IC and yearly/recent stability.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
