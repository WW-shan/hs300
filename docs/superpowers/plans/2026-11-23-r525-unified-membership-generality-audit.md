# R525 Unified Membership Generality Audit Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to plan task by task.

**Goal:** Explain why R385/R386 passed historical-membership gates while R499/R506 failed current-membership gates, and quantify the membership-basis effect without pretending either candidate is production-ready.

**Architecture:** Read-only lineage/metric audit. Compare the historical-membership R385 candidate against the current-membership R506 candidate on available common dimensions; do not infer a winner and do not recompute until the frozen input bundle is committed/reproducible.

## Constraints

- No new strategy selection.
- No direct headline-return ranking across different membership vintages.
- No leverage, orders, paper targets or production activation.
- Both candidates remain research-only.

## Tasks

1. Freeze sources and hypotheses; add failing tests.
2. Extract R385 historical-membership metrics and R506 current-membership metrics.
3. Record what the difference proves and what it cannot prove.
4. Write JSON/Markdown/sidecars.
5. Verify, commit, push and check CI.
