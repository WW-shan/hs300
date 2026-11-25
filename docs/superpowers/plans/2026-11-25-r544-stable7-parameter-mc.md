# R544 Stable7 Parameter Monte Carlo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development.

**Goal:** 固定 R543 七因子，在 108 组执行参数中用训练期平台和 5000 次 bootstrap 选稳健参数。

**Spec:** `docs/superpowers/specs/2026-11-25-r544-stable7-parameter-mc-design.md`

---

### Task 1: 参数与选择规则测试

- [x] 测试 108 组网格完整性。
- [x] 测试训练资格边界。
- [x] 测试邻域平台优先于孤立尖峰。
- [x] 测试最终 bootstrap 选择规则。
- [x] 测试 5× warning-only 和 fresh-start gate。

### Task 2: 训练期正式网格

- [x] 复用 R543 stable7 panel。
- [x] 每个 hold/stop 准备一次 prepared matrix。
- [x] 跑 108 次训练期 2× 正式回测。
- [x] 保存证据和 metrics，不保存所有 trades/curve 到结果。

### Task 3: 平台与 MC

- [x] 计算 108 点邻域统计。
- [x] 取前 5 个平台点。
- [x] 5000 次 block bootstrap。
- [x] 选择最终参数。

### Task 4: 确认

- [x] full/recent 2×/3×/5×。
- [x] validation 2×。
- [x] 6 fresh starts。
- [x] full/recent 5000 次 bootstrap。
- [x] 与 R543 默认参数和 HS300/等权对照。

### Task 5: 发布和审计

- [x] 预注册 108+13 次正式运行。
- [x] 发布 JSON/Markdown/sidecar/ResearchLedger。
- [x] 资产台账 v38、conformance、contract、hygiene。
- [x] Ruff、聚焦测试、全量测试、前端 build、diff check。

### Task 6: 提交和 CI

- [x] DCO 提交推送。
- [x] 检查 CI/Docker。
