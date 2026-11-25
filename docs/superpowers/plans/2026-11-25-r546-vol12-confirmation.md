# R546 vol_12 Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development.

**Goal:** 确认 R545 训练表中收益更高、回撤仍改善的 vol_12 风险预算，不搜索新参数。

**Spec:** `docs/superpowers/specs/2026-11-25-r546-vol12-confirmation-design.md`

---

### Task 1: 确认规则测试

- [x] 测试 vol_12 变体和 13 次正式运行冻结。
- [x] 测试 gate 与 5× warning-only。
- [x] 测试 non-independent OOS 标记。

### Task 2: 正式确认

- [x] 复用 R545 风险预算 compute 和项目 resolver 注入。
- [x] full/recent 2×/3×/5×。
- [x] validation 2×。
- [x] 6 fresh starts 2×。
- [x] full/recent bootstrap 5000 次。

### Task 3: 对照与发布

- [x] 与 R543、R545、HS300、PIT 等权比较。
- [x] 预注册 source hashes 和 13 次运行。
- [x] 发布 JSON/Markdown/sidecar/ResearchLedger。
- [x] 资产台账 v40、conformance、contract、hygiene。
- [x] 全量测试、前端 build、diff check。

### Task 4: 提交和 CI

- [x] DCO 提交推送。
- [x] 检查 CI/Docker。
