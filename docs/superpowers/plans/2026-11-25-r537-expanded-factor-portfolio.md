# R537 Expanded Factor Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 13 个独立簇代表构建等权组合，并在项目正式回测中与 6 因子核心对照。

**Architecture:** 复用 R533 formal runner、R536 finalist mapping 和 R531 rank-frame；不新增参数搜索。

**Spec:** `docs/superpowers/specs/2026-11-25-r537-expanded-factor-portfolio-design.md`

---

### Task 1: 构造扩展组合评分

- [ ] 测试 13 个代表和冻结参数映射。
- [ ] 复用 `_core_frame` 构造 13 因子 rank composite。
- [ ] 保留 R533 6 因子 core 作为对照。

### Task 2: 正式回测确认

- [ ] 跑扩展组 full/recent 2×/3×/5×、validation 2×、6 个 fresh starts。
- [ ] 跑 core 对照 full/recent 2×。
- [ ] 映射 gate_policy_v2，保持 5× warning-only。

### Task 3: 发布和审计

- [ ] 预注册 source hashes、组合成员、执行参数和门禁。
- [ ] 不可覆盖发布 JSON/Markdown + ResearchLedger。
- [ ] 更新 v29 asset ledger、conformance/contract/hygiene。

### Task 4: 验证与提交

- [ ] Ruff、聚焦测试、research audits、`git diff --check`。
- [ ] 后端全量测试、前端构建。
- [ ] DCO 提交、推送、CI/Docker。
