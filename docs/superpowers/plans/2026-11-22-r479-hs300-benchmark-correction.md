# R479 HS300 Benchmark Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Correct the benchmark label for R477 by reading the local `000300.SH` index series instead of the engine's `000001.SH` benchmark field.

**Architecture:** Append-only correction. Reuse the frozen R477 curve and trade evidence, compute the matching `000300.SH` window return from `data/kline_index_daily`, and write a separate corrected report. R477 remains untouched.

## Constraints

- Do not rerun or reselect the strategy.
- Do not overwrite R477.
- `000300.SH` is the only benchmark for the corrected table.
- R477's former benchmark is explicitly labeled `000001.SH (上证指数)`.
- No leverage, orders or production activation.

## Tasks

1. Freeze the correction source and windows; add a failing test for the benchmark symbol.
2. Load `000300.SH` close-to-close returns for both R477 windows.
3. Recompute excess return and write JSON/Markdown/sidecar.
4. Run verification, commit and push.

## Acceptance

- Corrected benchmark symbol is exactly `000300.SH`.
- Both windows have non-empty benchmark curves.
- R477 remains unchanged and rejected.
- Checksums verify.
