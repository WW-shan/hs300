# GBM Comprehensive Market Stress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development. Execute tasks in order.

**Goal:** 用项目矩阵撮合链跑完 GBM 的 4 情景 × 2 相关性 × 6 信号 × 2 止损 × 3 成本 × 100 路径，并发布只读诊断证据。

**Spec:** `docs/superpowers/specs/2026-11-25-gbm-comprehensive-market-stress-design.md`

---

### Task 1: 生成器和信号契约

**Files:**
- Create: `backend/app/backtest/synthetic_gbm.py`
- Test: `backend/tests/backtest/test_synthetic_gbm.py`

- [x] 先写失败测试：同 seed 确定性、不同路径发散、OHLC/成交量不变量。
- [x] 先写失败测试：6 种 score 的 shape、范围、warmup 和未来价格隔离。
- [x] 实现 `generate_synthetic_market` 与 `compute_signal_scores`，最小通过测试。
- [x] 用 300 股测试数据核对横截面百分位和行业映射。

### Task 2: 项目撮合集成

**Files:**
- Modify: `backend/app/backtest/synthetic_gbm.py`
- Test: `backend/tests/backtest/test_synthetic_gbm.py`

- [x] 先写失败测试：`entry_delay_bars=1` 使 t 信号在 t+1 开盘生效。
- [x] 先写失败测试：2×/3×/5×成本表和 no-leverage/100% 敞口。
- [x] 实现 `build_project_market_matrix`、`matcher_config`、`simulate_strategy`。
- [x] 实现同路径 300 股等权买入持有矩阵与 benchmark 仿真。
- [x] 断言策略与 benchmark 均通过 `BacktestEngine.simulate_market_matrix`。

### Task 3: 路径运行和聚合

**Files:**
- Modify: `backend/app/backtest/synthetic_gbm.py`
- Test: `backend/tests/backtest/test_synthetic_gbm.py`

- [x] 先写失败测试：一条路径产生 6×2×3 个策略记录和 3 个 benchmark。
- [x] 先写失败测试：聚合必须拒绝缺 cell 或每 cell 路径数不足。
- [x] 实现 path job、paired excess、p05/p50/p95、win rate、sign-test p。
- [x] 实现成本单调性、止损差异、beta 差异的汇总。

### Task 4: 研究运行器

**Files:**
- Create: `scripts/research_gbm_market_stress.py`
- Modify: `backend/tests/backtest/test_synthetic_gbm.py`

- [x] 顶层声明 `RESEARCH_TYPE="synthetic_market_stress"` 与 `RESEARCH_PREREGISTRATION`。
- [x] 实现 `--preflight`、`--freeze-preregistration`、`--smoke-paths` 和完整运行。
- [x] 预注册绑定 runner + generator 的 SHA-256、seed、100 路径、全部 gate。
- [x] 结果 envelope 固定 `diagnostic_only`，`active_version=null`。
- [x] JSON 只保存聚合分布；Markdown 输出全部 288 个 cell 的摘要和边界。
- [x] 通过 `publish_research_artifacts` 发布不可变 JSON/Markdown/sidecar/ledger。

### Task 5: 研究资产与审计

**Files:**
- Modify: `scripts/audit_research_research_assets.py`
- Modify: `backend/tests/test_research_hygiene.py`

- [x] 先写失败测试：asset ledger 必须接受 `synthetic_market_stress` 且归为诊断负知识。
- [x] 把审计输出版本推进到 `research_asset_ledger_2026-11-25_v35.json`。
- [x] 更新 hygiene 测试读取 v35。
- [x] 重新生成 conformance、asset、hygiene、contract 审计及 sidecar。

### Task 6: 完整执行与发布

- [x] 运行小样本 smoke，核对 no lookahead、benchmark、成本三档。
- [x] 冻结预注册和 sidecar；冻结后不得再改 runner/generator。
- [x] 使用固定 seed 跑完整 800 条路径任务（每个 scenario×beta×path 一次），并流式聚合。
- [x] 发布 result JSON/Markdown/sidecar/ResearchLedger；检查 288 cells 和 28,800 条策略观测。
- [x] 审查 JSON/Markdown 是否只给 `diagnostic_only`，并列出成本/止损/相关性诊断。

### Task 7: 验证与提交

- [x] `uv run ruff check` 新模块、脚本、测试。
- [x] 聚焦 pytest 通过。
- [x] contract/conformance/asset/hygiene 审计通过。
- [x] 全量后端 pytest、前端 build、`git diff --check` 通过。
- [x] `git status` 确认无合成价格数组、缓存、临时文件或绝对路径。
- [x] DCO 提交（`git commit -s`），推送 `main`。
- [x] 检查 GitHub CI 与 Docker workflow；失败则修复后重新验证和推送。
