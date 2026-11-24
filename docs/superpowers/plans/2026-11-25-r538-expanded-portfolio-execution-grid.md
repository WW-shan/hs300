# R538 Expanded Portfolio Execution Grid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在冻结 13 因子成员下，用训练期 MC 选择持仓/持有期和行业仓位上限，再正式确认。

**Architecture:** 复用 R537C 扩展评分、R533 formal helper 和 v2 gate；只新增执行网格和行业 cap。

**Spec:** `docs/superpowers/specs/2026-11-25-r538-expanded-portfolio-execution-grid-design.md`

---

### Task 1: 网格与选择规则

- [ ] 测试 9 个执行组合和行业 cap。
- [ ] 测试训练资格与确定性排序。

### Task 2: 正式训练和确认

- [ ] 9 个训练正式回测 + 5000 次 bootstrap。
- [ ] 确认 full/recent/validation/fresh starts/control。
- [ ] 映射 gate_policy_v2。

### Task 3: 发布与审计

- [ ] 预注册、不可覆盖发布、ResearchLedger、v31 asset ledger。

### Task 4: 验证提交

- [ ] Ruff、聚焦测试、research audits、全量测试、构建、DCO 提交推送。
