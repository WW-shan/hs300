# R534 Regime-Gated Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在冻结的 R533 六因子组合上验证 T-1 市场情绪/状态门控能否修复近一年负收益。

**Architecture:** 新 runner 复用 R533 的六因子评分、执行参数和 formal helper，只增加预注册的 6 个 regime 规则。训练期按 block bootstrap 尾部 MDD 选择，之后通过项目 run_with_evidence 链确认全样本、近一年、3×、5×、fresh starts 和两因子控制。

**Tech Stack:** Python 3.11、Polars、项目 regime filter、StrategyBacktestService、gate_policy_v2、Pytest。

**Spec:** `docs/superpowers/specs/2026-11-25-r534-regime-gated-core-design.md`

---

### Task 1: 冻结来源和规则

**Files:**
- Create: `backend/tests/test_r534_regime_gated_core.py`
- Create: `scripts/research_r534_regime_gated_core.py`

- [ ] 写失败测试，断言 R533A/R533B sidecar、10/60 参数和 6 个规则固定。
- [ ] 实现 `load_source()` 和 `regime_rules()`。
- [ ] 运行聚焦测试通过。

### Task 2: 训练期正式回测与选择

**Files:**
- Modify: `scripts/research_r534_regime_gated_core.py`
- Test: `backend/tests/test_r534_regime_gated_core.py`

- [ ] 写失败测试覆盖 eligibility 和 MDD-tail-first 排序。
- [ ] 用 `regime_filter` 构造 prepared matrix，跑 6 个训练正式回测和 5000 次 bootstrap。
- [ ] 断言 always-on 复现 R533 训练指标。
- [ ] 只按训练期选择规则。

### Task 3: 确认和 gate-policy-v2

**Files:**
- Modify: `scripts/research_r534_regime_gated_core.py`
- Test: `backend/tests/test_r534_regime_gated_core.py`

- [ ] 跑 always-on、选中规则、两因子 control、成本压力、验证、近一年和 fresh starts。
- [ ] 映射 v2 的 `annual`/`mdd`，保持 5× warning-only。
- [ ] 额外检查近一年选中规则优于 always-on。

### Task 4: 发布与审计

- [ ] 预注册来源、规则、窗口、seed、门禁和 post-hoc 限制。
- [ ] 不可覆盖发布 result/Markdown 和 ResearchLedger。
- [ ] 更新 v26 asset ledger、conformance/contract/hygiene audits。

### Task 5: 验证与提交

- [ ] Ruff、聚焦测试、contract/conformance/hygiene/assets、`git diff --check`。
- [ ] 后端全量测试和前端构建。
- [ ] DCO 提交、推送、CI/Docker 轮询。
