# R504 R499 Recent Relative-Failure Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Diagnose why the frozen R499 sleeve lost relative to HS300 in the latest rolling years, without changing any weights. Separate concentration in a few winners, industry/size exposure, month-level path, and weak-state timing.

**Architecture:** Append-only behavioral attribution. Recompute the frozen R499 recent year, inspect trade-level concentration, remove top winners, compute monthly and industry contributions, and compare weak/nonweak timing. No reselection or promotion.

## Constraints

- Freeze R499 weights and threshold exactly.
- Diagnostics only; no parameter tuning.
- Test/recent are not new holdouts.
- Industry classification is a current snapshot.
- 5x remains warning-only.

## Tasks

1. Freeze source and diagnostic fields; add failing tests.
2. Recompute frozen curve and trade ledger for the recent year.
3. Compute concentration, monthly/industry and weak-state diagnostics.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
