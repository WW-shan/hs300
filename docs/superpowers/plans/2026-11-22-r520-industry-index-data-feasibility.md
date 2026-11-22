# R520 Industry Index Data Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Verify whether the available Shenwan and Tonghuashun industry-index endpoints can supply a genuine 2018-start industry-rotation dataset, and persist a research-only inventory.

**Architecture:** Read-only provider probe plus append-only research inventory. Fetch industry catalogs, validate long-history coverage, and record only metadata and small source samples. No full production sync.

## Constraints

- No strategy backtest in this round.
- No production data preference change.
- No leverage and no production activation.
- Current ETF/index metadata is not treated as historical industry composition.

## Tasks

1. Freeze sources, coverage gates and sample industries; add failing tests.
2. Fetch Shenwan and Tonghuashun industry catalogs.
3. Validate history start/end for catalog members and save research inventory.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
