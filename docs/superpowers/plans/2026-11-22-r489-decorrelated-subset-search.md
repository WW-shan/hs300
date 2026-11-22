# R489 Correlated-Leg Removal and Decorrelated Subset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Find a smaller 6–8 leg subset of the R480 library whose members are less correlated and whose test/recent tail is better than R484, without using test/recent for selection.

**Architecture:** Append-only subset search over 8 frozen legs. Train+validation daily rank composites determine subset eligibility and ranking; only selected subsets are confirmed on test/recent. Correlation and marginal contribution are diagnostics, not post-test filters.

## Constraints

- No leverage; equal weights, sum=1.
- R484 cap=1, 10 positions, 40-day hold and scores remain fixed.
- Candidate subset size: 6/7/8 legs.
- Max subsets: all non-trivial combinations, but deterministic and capped at pre-registered 64 highest train+validation candidates.
- Test/recent are confirmation only.
- 5x warning-only.

## Tasks

1. Freeze subset universe, selection caps and gates; add failing tests.
2. Build R480 panel and generate all deterministic subsets.
3. Run train+validation for subsets; rank and select.
4. Confirm selected subset on test/recent/3x/5x + bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.

## Acceptance

- Every subset has 6–8 unique legs and normalized weights.
- Selection uses train+validation only.
- Test/recent never enter ranking.
- Final status follows all gates.
