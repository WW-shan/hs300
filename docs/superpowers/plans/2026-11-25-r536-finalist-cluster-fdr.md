# R536 Finalist Cluster FDR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 R535A 的 22 个 finalist 在训练期聚成独立暴露簇，并按簇代表重做 FDR。

**Architecture:** 复用 R535A 最终参数和统计、R531 训练期相关聚类；只新增 representative 选择和 cluster-level BH-FDR。

**Tech Stack:** Python 3.11、Polars、R530/R531 helpers、BH-FDR、ResearchLedger、Pytest。

**Spec:** `docs/superpowers/specs/2026-11-25-r536-finalist-cluster-fdr-design.md`

---

### Task 1: 冻结 22 finalist 和候选参数

- [ ] 写失败测试断言 finalist=22、source hash 有效。
- [ ] 实现 `load_finalists()` 和 family/parameter 映射。

### Task 2: 训练期相关聚类

- [ ] 写失败测试覆盖 cluster/representative/ICIR tie-break。
- [ ] 构造 finalist factor frames 和 training month-end correlations。
- [ ] 0.70 union-find 聚类，选择代表。

### Task 3: Cluster-level FDR 和结果

- [ ] 对 representatives 的 validation p 做 BH-FDR。
- [ ] 沿用 R535A 其它门槛，输出 admitted/rejected/inconclusive。
- [ ] 不可覆盖发布 JSON/Markdown + ResearchLedger。

### Task 4: 审计与提交

- [ ] 更新 v28 asset ledger、conformance/contract/hygiene。
- [ ] Ruff、聚焦测试、`git diff --check`。
- [ ] 后端全量测试、前端构建、DCO 提交、推送、CI/Docker。
