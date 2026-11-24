# R533 Core Formal Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将冻结的 6 因子核心接入项目正式 T+1 回测，按训练期 MC 选执行参数，再用验证/近一年和 v2 分层门禁确认。

**Architecture:** 新 runner 复用 R532 的冻结链和核心 rank 构造；只新增组合评分面板、9 组执行参数训练网格、正式证据回测和 v2 门禁映射。所有正式运行直接调用 `run_with_evidence` 并紧跟 `require_formal_evidence`，结果不可覆盖发布。

**Tech Stack:** Python 3.11、Polars、项目 `StrategyBacktestService`/`BacktestEngine`、`gate_policy_v2`、Pytest、ResearchLedger。

**Spec:** `docs/superpowers/specs/2026-11-25-r533-core-formal-portfolio-design.md`

---

### Task 1: 冻结 R532 输入和评分面板

**Files:**
- Create: `backend/tests/test_r533_core_formal_portfolio.py`
- Create: `scripts/research_r533_core_formal_portfolio.py`

**Interfaces:**
- Produces: `load_frozen_inputs(root: Path) -> dict[str, Any]`
- Produces: `build_score_panel(inputs, representative_ids) -> pl.DataFrame`
- Produces: `RESEARCH_TYPE = "project_formal"`

- [ ] 写失败测试，断言 R530/R531/R532 链有效、6 个因子固定、评分在 0-100 且非成员日无评分。
- [ ] 实现 sidecar/hash 校验和复用 R532 的 `load_study_inputs`、`build_core`。
- [ ] 实现主评分与固定的两因子 control 评分。
- [ ] 运行聚焦测试通过。

### Task 2: 正式回测执行与训练期 MC

**Files:**
- Modify: `scripts/research_r533_core_formal_portfolio.py`
- Test: `backend/tests/test_r533_core_formal_portfolio.py`

**Interfaces:**
- Produces: `training_candidates() -> list[dict[str, Any]]`
- Produces: `select_training_candidate(rows) -> dict[str, Any] | None`
- Produces: `run_formal_window(...) -> dict[str, Any]`

- [ ] 写失败测试覆盖 3×3 参数网格、固定 seed、资格门槛和确定性排序。
- [ ] 实现 prepared market matrix、评分列注入、历史成员并集和 T+1 正式运行。
- [ ] 对 9 个训练候选各做 5000 次 block-20 bootstrap，只按训练结果选一个。
- [ ] 断言每次正式结果都有 saved/passed/passed 证据。

### Task 3: 确认窗口、相位和集中度

**Files:**
- Modify: `scripts/research_r533_core_formal_portfolio.py`
- Test: `backend/tests/test_r533_core_formal_portfolio.py`

**Interfaces:**
- Produces: `concentration_metrics(trades, industries, max_positions) -> dict[str, Any]`
- Produces: `gate_payload(...) -> dict[str, Any]`

- [ ] 写失败测试覆盖毛利润 topN、最大行业、最大单票亏损和宽度。
- [ ] 运行验证、近一年、全样本 2×/3×/5× 和 6 个 fresh start 正式回测。
- [ ] 映射 `evaluate_gate_policy_v2`，保持 5× 仅 warning。
- [ ] 运行匹配两因子对照的全样本和近一年回测。

### Task 4: 预注册、发布和审计

**Files:**
- Create: `data/factor_research/research/r533_core_formal_portfolio_preregistration.json` + sidecar
- Create: `data/factor_research/research/r533_core_formal_portfolio_result.json` + sidecar
- Create: `data/factor_research/research/r533_core_formal_portfolio_result.md` + sidecar
- Modify: ledger、v25 asset ledger、conformance/contract audits、hygiene test references

- [ ] `--preflight` 验证 6 因子、9 候选、历史成员并集和 formal helpers。
- [ ] `--freeze-preregistration` 绑定 runner/input hashes、窗口、seed、门禁和 no-execution policy。
- [ ] 运行一次并发布，拒绝覆盖。
- [ ] 检查 `formal_evidence`、conclusion、5× warning-only、active_version=null。

### Task 5: 全量验证与提交

- [ ] 运行聚焦测试、contract/conformance/hygiene/assets audits、Ruff、`git diff --check`。
- [ ] 运行后端全量测试和前端构建。
- [ ] 检查最终 diff 和临时文件，`git commit -s` 并推送。
- [ ] 轮询 GitHub CI 与 Docker；失败先修复。
