# R542 Recent Failure Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development.

**Goal:** 冻结 R539C，完整拆解最近一年亏损来源，并发布项目正式证据。

**Spec:** `docs/superpowers/specs/2026-11-25-r542-recent-failure-attribution-design.md`

---

### Task 1: 纯函数与测试

- [x] 写失败测试：因子 quintile spread、留一法贡献符号、回撤区间、组合 beta、regime 归因。
- [x] 实现纯函数并跑绿。
- [x] 覆盖边界：空数据、缺少 benchmark 日期、零波动。

### Task 2: 冻结 runtime 与因子诊断

- [x] 复用 R537/R538/R539 runtime，不重建数据链。
- [x] 计算 13 因子 train/validation/recent/full IC、neutral IC、quintile spread 和 coverage。
- [x] 计算 HS300 与 PIT 成分等权基准。

### Task 3: 正式组合归因

- [x] 运行 baseline full/recent 2x；
- [x] 运行 baseline no-stop full/recent；
- [x] 运行 13 个 single-leg full/recent；
- [x] 运行 13 个 leave-one-out full/recent；
- [x] 每次运行调用 `run_with_evidence()` + `require_formal_evidence()`；
- [x] 验证 baseline recent/full 与 R539C 对齐。

### Task 4: 风险、交易和市场状态

- [x] 计算 beta、月度、回撤区间、行业 PnL、交易集中度、止损占比。
- [x] 计算持仓规模分位和因子暴露。
- [x] 计算 regime 覆盖内日收益/超额，并记录覆盖缺口。

### Task 5: 发布和审计

- [x] 预注册 source hashes、参数、窗口和 56 次正式运行。
- [x] 发布 JSON/Markdown/sidecar/ResearchLedger。
- [x] 更新 asset ledger v36、conformance、contract、hygiene。
- [x] Ruff、聚焦测试、全量测试、前端 build、git diff check。

### Task 6: 结论与后续

- [x] 按 IC、LOO、单一因子、beta、行业、成本和 regime 给出归因。
- [x] 明确 R543 应该只修一个最大瓶颈。
- [x] DCO 提交并推送，检查 CI/Docker。
