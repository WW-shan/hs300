# R522 R506 Fresh-Start One-Year Audit Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Freeze the current best research candidate R506 and check five fresh-start one-year windows, so the reported “recent year still loses money” claim is tied to a reproducible strategy version and window.

**Architecture:** Read-only confirmation over the existing R506 executor. No factor, parameter, cost or gate changes. Reuse R506 weights/cap-1 execution, run five starts spread across the recent year, and write JSON/Markdown/sidecars.

## Constraints

- R506 remains research-only; no active version, orders, paper targets or production.
- No parameter selection and no test-window tuning.
- 2x stock cost is the confirmation cost; 3x stress and 5x warning.
- Fresh starts overlap heavily and are not independent holdouts.
- Current HS300 membership retains survivorship bias.

## Tasks

1. Freeze five starts and gates; add failing tests.
2. Run frozen R506 for each one-year window plus benchmark.
3. Record return, Sharpe, MDD, trades, 3x stress and block-bootstrap tails.
4. Write JSON/Markdown/sidecars.
5. Verify, commit, push and check CI.
