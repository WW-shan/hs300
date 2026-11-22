# R519 Macro Conditioning Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether the fully covered macro conditioners distinguish R506 sleeve performance, using lagged expanding terciles and strict multiple-testing gates.

**Architecture:** Append-only conditional IC audit. Reuse R513's sleeve definitions and neutralization, replace conditioners with R518's full-history set, and report high-minus-low IC with NW/BH gates.

## Constraints

- Only R518 full-history conditioners.
- One-day lag and expanding historical terciles.
- No portfolio backtest in this round.
- No leverage and no production activation.

## Tasks

1. Freeze conditioners and gates; add failing tests.
2. Build R506 sleeve scores and lagged macro labels.
3. Compute conditional neutral IC.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
