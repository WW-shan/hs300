# R512 Growth Acceleration Significance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Add the preregistered Newey-West and BH-FDR significance gates that R511 did not persist, without changing the R511 composites.

**Architecture:** Append-only correction. Recompute the two R511 composites exactly, compute neutral rank IC and NW/BH gates, and preserve R511 unchanged.

## Constraints

- Exactly the two R511 composites.
- No new factors or weights.
- Same PIT attachment and neutralization.
- No portfolio backtest or promotion.
- 5x warning-only; no leverage.

## Tasks

1. Freeze source hashes and gates; add failing tests.
2. Recompute R511 composites.
3. Compute neutral IC, NW p and BH q.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
