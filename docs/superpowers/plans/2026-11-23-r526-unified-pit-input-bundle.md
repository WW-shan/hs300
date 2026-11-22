# R526 Unified PIT Input Bundle Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to plan task by task.

**Goal:** Turn the R381 repaired membership and PIT industry lineage into a small, committed, digest-bound input bundle so a later re-run of both strategy families can use exactly the same data vintage.

**Architecture:** Research artifact export, not raw market data. Copy only the small membership, industry-history and required-extension tables into a versioned bundle; write a manifest with row counts, dates and SHA-256; add a test that verifies the bundle against a frozen manifest.

## Constraints

- No backtest in this round.
- No production data sync, no provider preference change, no orders.
- The bundle is a research input, not a tradable result.
- R381 membership must remain 300 symbols per session and the industry history must retain its effective dates.

## Tasks

1. Freeze the bundle manifest and checksum; add failing tests.
2. Export the three small research inputs to a committed bundle.
3. Verify row counts, session counts, symbol counts and hashes.
4. Write JSON/Markdown/sidecars.
5. Commit, push and check CI.
