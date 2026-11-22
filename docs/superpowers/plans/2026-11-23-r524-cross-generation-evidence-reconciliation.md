# R524 Cross-Generation Evidence Reconciliation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Reconcile the R385/R386 historical-membership candidate with the R499/R506 current-membership candidate without pretending they are directly comparable, and fix the authoritative next comparison.

**Architecture:** Read-only evidence audit. Validate source hashes and sidecars, extract data lineage, windows, costs, membership basis and gates, then emit a comparison matrix plus one explicit comparability verdict. No backtest, no reselection and no promotion.

## Constraints

- R385/R386 used repaired daily HS300 membership and effective-date Shenwan industry fields.
- R499/R506 use current official HS300 membership applied to history.
- The two families are not directly comparable; neither result supersedes the other.
- No leverage, orders, paper targets or production activation.

## Tasks

1. Freeze the source set and comparability gates; add failing tests.
2. Validate hashes, windows, membership basis, cost roles and status fields.
3. Write JSON/Markdown/sidecars with an apples-to-oranges verdict.
4. Verify, commit, push and check CI.
