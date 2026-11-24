# R540 Weighted Expanded Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用训练期 ICIR 权重和 Top-N 方案压缩 13 因子组合，确认能否修复近一年。

**Architecture:** 复用 R539C 执行、R536A 训练 ICIR 和 R537 扩展因子 frames；新增四种固定加权评分和训练期选择。

**Spec:** `docs/superpowers/specs/2026-11-25-r540-weighted-expanded-core-design.md`

---

### Task 1: 权重方案与评分

- [ ] 测试四种权重、正 ICIR 归一化和 Top-N。
- [ ] 构造训练期 ICIR 权重。
- [ ] 构造加权 rank score panel。

### Task 2: 训练和确认

- [ ] 四方案训练正式回测 + 5000 bootstrap。
- [ ] 选中方案 full/validation/recent/fresh/3×/5×。
- [ ] equal control 和 v2 门禁。

### Task 3: 发布审计

- [ ] 预注册、不可覆盖发布、ResearchLedger、v33 asset ledger。

### Task 4: 验证提交

- [ ] Ruff、聚焦、research audits、全量测试、构建、DCO 提交。
