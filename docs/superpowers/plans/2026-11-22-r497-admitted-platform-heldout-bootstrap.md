# R497 Admitted Platform Held-Out Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to add a pre-registered held-out bootstrap confirmation for the R496 selected admitted platform.

**Goal:** Reconfirm the R496 selected platform with a fresh bootstrap seed and explicit HS300-relative paired tail, without changing selection.

**Architecture:** Append-only confirmation of the frozen R496 selected sample.

## Tasks

1. Freeze the R496 selected sample and new seeds.
2. Recover the frozen curve and run absolute and paired-excess bootstrap.
3. Write JSON/Markdown/sidecar.
4. Verify, commit and push.

## Acceptance

- R496 sample and parameters unchanged.
- New bootstrap seeds are distinct from R496.
- R496 remains rejected unless all gates pass.
