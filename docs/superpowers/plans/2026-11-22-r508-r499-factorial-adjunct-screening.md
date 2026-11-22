# R508 R499 Low-Weight Adjunct Factor Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Test whether the remaining statistical-pass factors that are not already in the R499 core can add value as low-weight adjuncts, rather than replacing a core leg.

**Architecture:** Append-only adjunct screen. Freeze the R506 cap-1 base, add one candidate at a time at 5%/10% weight, compute real T+1 train/validation and only then test/recent. Parameter sensitivity is limited to the candidate's declared parameter family, not a free-form search.

## Constraints

- Base is R506 cap-1 with fixed weights and threshold.
- Candidates must be R391 `statistical_pass` and not already in R499.
- At most one candidate per variant; no combinatorial search.
- Selection only on train+validation.
- No leverage; total weight <=1.
- 2x main, 3x stress, 5x warning-only.

## Tasks

1. Freeze the candidate list and weight grid; add failing tests.
2. Recompute R506 cap-1 base.
3. Run adjunct train/validation for each candidate.
4. Confirm any eligible candidate on test/recent/3x/5x and paired bootstrap.
5. Write JSON/Markdown/sidecar; verify, commit and push.
