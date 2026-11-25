# R543 Stable IC Subset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development.

**Goal:** 验证 train+validation 稳定 IC 的 7 因子子集是否在最近一年转正且保留可接受的风险收益。

**Spec:** `docs/superpowers/specs/2026-11-25-r543-stable-ic-subset-design.md`

---

### Task 1: 规则与门槛测试

- [x] 写失败测试：稳定因子选择只使用 train/validation IC。
- [x] 写失败测试：核心门槛边界和 5x warning-only。
- [x] 写失败测试：fresh-start 正数和中位数逻辑。
- [x] 实现纯函数并跑绿。

### Task 2: 正式候选

- [x] 复用 R542 runtime 和 `build_member_panel`。
- [x] 验证稳定集合正好 7 个。
- [x] full/recent 2x/3x/5x、validation 2x、6 fresh starts。
- [x] 每次运行 guard + formal evidence。

### Task 3: Bootstrap 与对照

- [x] full/recent 2x 各 5000 次 block bootstrap。
- [x] 与 R539C、沪深300、PIT 等权比较。
- [x] 报告 full 收益下降与 recent/MDD 改善的取舍。

### Task 4: 发布与审计

- [x] 预注册 R542 source hash、7 因子、13 次运行和门槛。
- [x] 发布 JSON/Markdown/sidecar/ResearchLedger。
- [x] 资产台账 v37、conformance、contract、hygiene。
- [x] Ruff、聚焦测试、全量测试、前端 build、diff check。

### Task 5: 提交与 CI

- [x] DCO 提交推送。
- [x] 检查 CI/Docker。
