# R531 因子库核心 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 R530 的 11 个准入候选构建训练期聚类、验证期检验的因子库核心，不重新调参。

**Architecture:** 新增 `scripts/research_r531_factor_library_core.py`，复用 R530 的 PIT 上下文、候选声明和因子计算器。相关性和聚类只在训练期月末计算，代表规则冻结后计算验证/近期核心 IC 和 MC。

**Spec:** `docs/superpowers/specs/2026-11-25-r531-factor-library-core-design.md`

### Task 1: 冻结 R530 准入集与合同声明

- [ ] Test: admitted candidate count is exactly 11; source result/prereg hashes verify.
- [ ] Implement `load_admitted_candidates()` from R530 result and R530 candidate mapping.
- [ ] Add `RESEARCH_TYPE="statistical_screen"` and prereg path.
- [ ] Run focused tests; commit.

### Task 2: 训练期相关矩阵和聚类

- [ ] Test union-find correlation clustering and representative choice on a fixture.
- [ ] Implement month-end panel, pairwise cross-sectional Spearman correlation, threshold 0.70, deterministic representative by R530 train IC.
- [ ] Verify validation/recent data is not used in clustering.
- [ ] Commit.

### Task 3: 核心 rank 因子与 5000 次 MC

- [ ] Test daily percent-rank aggregation and deterministic bootstrap.
- [ ] Implement `library_core_rank` from representative calendar-day percentiles.
- [ ] Compute neutralized full/train/validation/recent IC and 5000-run block bootstrap 10/20/40.
- [ ] Apply gates and produce `admitted_for_research`/`rejected`/`inconclusive`.

### Task 4: 发布 evidence

- [ ] Freeze preregistration with source hashes, 11 candidates, threshold, windows, seed and output policy.
- [ ] Run once with immutable `publish_research_artifacts`.
- [ ] Verify sidecars, ledger record, no active/orders/paper/prod.
- [ ] Run contract/conformance/hygiene/assets audits.

### Task 5: 全量验证与推送

- [ ] Run focused tests, full backend tests, frontend build, Ruff.
- [ ] Commit DCO-signed changes.
- [ ] Push main and inspect CI and Docker.
