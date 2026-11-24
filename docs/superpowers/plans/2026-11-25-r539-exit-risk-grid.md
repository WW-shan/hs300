# R539 Exit Risk Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 R538C 上训练期选择 stop/trailing 组合并正式确认近期风险改善。

**Architecture:** 复用 R538C 13 因子评分和 R533 formal/v2 门禁，只增加退出 overrides。

**Spec:** `docs/superpowers/specs/2026-11-25-r539-exit-risk-grid-design.md`

---

### Task 1: 固定 7 个退出规则

- [ ] 测试规则数量、顺序和默认控制。
- [ ] 实现训练资格和确定性排序。

### Task 2: 训练与确认

- [ ] 6/7 个候选分别构造带 stop/trailing overrides 的 prepared matrix。
- [ ] 训练 5000 次 bootstrap。
- [ ] 确认 full/recent/validation/fresh/3×/5× 和默认控制。

### Task 3: 发布与审计

- [ ] 预注册、不可覆盖发布、ResearchLedger、v32 asset ledger。

### Task 4: 验证提交

- [ ] Ruff、聚焦、research audits、全量测试、构建、DCO 提交。
