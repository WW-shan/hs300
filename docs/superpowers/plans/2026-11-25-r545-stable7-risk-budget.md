# R545 Stable7 Risk Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development.

**Goal:** 固定 R543 七因子和执行参数，比较无杠杆现金/回撤/波动率预算，并训练期选一个稳健风险政策。

**Spec:** `docs/superpowers/specs/2026-11-25-r545-stable7-risk-budget-design.md`

---

### Task 1: 仓位策略纯函数测试

- [x] 测试固定现金预算。
- [x] 测试回撤 guard 边界。
- [x] 测试波动率目标只缩不放。
- [x] 测试组合预算取最小值。
- [x] 测试训练期选择规则和 9+13 次正式运行。

### Task 2: 训练期风险预算

- [x] 复用 R543 runtime 和 stable7 panel。
- [x] 生成 9 个 PositionSizingPolicy compute。
- [x] 跑 9 次训练期正式回测。
- [x] 按预注册门槛选一个变体。

### Task 3: 确认

- [x] full/recent 2×/3×/5×。
- [x] validation 2×。
- [x] 6 个 fresh starts。
- [x] 5000 次 bootstrap。
- [x] 与 R543/R539/HS300/等权比较。

### Task 4: 发布与审计

- [x] 预注册 22 次正式运行。
- [x] 发布 JSON/Markdown/sidecar/ResearchLedger。
- [x] 资产台账 v39、conformance、contract、hygiene。
- [x] Ruff、聚焦测试、全量测试、前端 build、diff check。

### Task 5: 提交和 CI

- [x] DCO 提交推送。
- [x] 检查 CI/Docker。
