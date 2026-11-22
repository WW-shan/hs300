# R481 Entry/Exit Neighborhood Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Test whether a small, pre-registered entry/exit threshold neighborhood around the R480 selected 8-leg subset can remove the negative bootstrap tails without sacrificing HS300 excess.

**Architecture:** Append-only neighborhood audit. Freeze the R480 subset and weights; vary only entry/exit score pairs from a small grid. Select on train+validation; confirm once on test/recent with 2x/3x and HS300 benchmark.

## Constraints

- Factor members, weights, positions, hold, exposure and costs are frozen from R480.
- Entry/exit pairs are pre-registered; no other parameter changes.
- Test/recent cannot influence selection.
- 5x is warning-only.
- Current HS300 survivorship remains explicit.

## Tasks

1. Freeze the neighborhood, gates and benchmark; add failing tests.
2. Rebuild the R480 selected 8-leg composite.
3. Run train+validation grid; select only from those windows.
4. Run one-shot test/recent/stress confirmation with bootstrap.
5. Verify, commit and push.

## Acceptance

- All samples have 8 legs and weights sum to 1.
- Only entry/exit scores vary.
- Promotion requires all return, Sharpe, MDD, HS300 excess and bootstrap gates.
- No leverage and no production activation.
