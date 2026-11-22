# R500 R499 Recent-Tail Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Explain the rejected R499 near-one-year tail without changing its weights or selection: identify losing months, whether losses are absolute or HS300-relative, whether they concentrate in weak-state or non-weak days, and which frozen legs underperform in the recent window.

**Architecture:** Append-only diagnostic on the frozen R499 sample. Recompute its exact recent/test curves, pair them by date with `000300.SH`, decompose monthly and state-conditioned returns, and recompute leg-level rank IC inside losing months. No parameter selection and no promotion.

## Constraints

- Freeze R499 weights, weak threshold and execution exactly.
- No new factor formulas and no weight/parameter search.
- Test/recent are descriptive windows only.
- Report absolute and paired-excess tail separately.
- 5x remains warning-only.

## Tasks

1. Freeze source hashes and diagnostic fields; add failing tests.
2. Recompute frozen R499 test/recent curves.
3. Compute monthly, weak-state and leg-level losing-month diagnostics.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
