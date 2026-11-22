# R521 Industry Index Momentum and Parameter Monte Carlo Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether Shenwan first-level industry-index momentum has stable, cost-surviving cross-sectional predictability from 2018, using a pre-registered parameter grid and block-bootstrap confirmation.

**Architecture:** Read-only research runner over external industry-index bars. Build a daily close panel for the 28 long-history industries, compute month-end signals from trailing information, execute at the next trading day open after a completed month-end signal, and persist parameter-level returns plus a small set of diagnostics. No raw bars are committed.

## Constraints

- Universe: only R520-qualified Shenwan industries with history start <= 2018-01-02.
- No leverage: sum of absolute target weights <= 1; residual is cash.
- No production activation, orders, or paper targets.
- Index-level results are not directly tradable; fund mapping is a separate future requirement.
- The test and recent windows have been observed before; this is a new pre-registered protocol, not a blind holdout.
- 2x cost is the main gate; 3x is stress; 5x is warning-only.
- Parameter selection uses train+validation only. Test/recent are confirmation only.

## Tasks

1. Freeze data source, universe rule, windows, parameter grid, costs, and gates; add failing tests.
2. Fetch and normalize qualified industry histories; assert no future leakage and full panel coverage.
3. Simulate monthly top-N momentum portfolios with cash residual and explicit turnover.
4. Run the pre-registered grid, block bootstrap, FDR, plateau, and cost stress.
5. Write JSON/Markdown/sidecars; verify, commit, push, and check CI.
