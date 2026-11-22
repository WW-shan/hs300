# R506 R499 Cap-1 Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Correct the R505 selection inconsistency by freezing the cap-1 variant, which had stronger train, validation and validation-tail metrics, and confirming it on test/recent/stress without any new selection.

**Architecture:** Append-only confirmation. Recompute only the frozen R499 factor set with `max_new_positions_per_day=1`, then run test/recent/3x/5x and paired HS300 bootstrap.

## Constraints

- Only daily cap changes: exactly 1.
- All other R499 parameters remain fixed.
- No comparison-based reselection.
- 2x main, 3x stress, 5x warning-only.
- No leverage.

## Tasks

1. Freeze cap-1 source hashes and gates; add failing tests.
2. Recompute train/validation to verify parity with R505 cap-1.
3. Confirm test/recent/3x/5x and paired bootstrap.
4. Write JSON/Markdown/sidecar.
5. Verify, commit and push.
