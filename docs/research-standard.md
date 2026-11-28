# 研究标准化与证据门禁

本文是 `data/factor_research`、`scripts/research_*.py` 和后续因子研究的执行规范。它不替代 [`CONTRIBUTING.md`](../CONTRIBUTING.md) 的数据契约、测试和复审要求；两者同时适用。

## 1. 五类研究

| 类型 | 用途 | 必须满足 |
| --- | --- | --- |
| `project_formal` | 产生组合收益、回撤、Sharpe 或策略结论 | 使用项目 `DataStore -> KlineRepository -> BacktestEngine -> StrategyBacktestService.run_with_evidence`；`persistence=saved`、`lookahead=passed`、`execution_timing=passed` |
| `statistical_screen` | 因子 IC、相关性、覆盖率、统计准入或参数稳定性 | 使用项目数据；写明输入哈希、统计方法和窗口；不得冒充组合收益结论 |
| `attribution` / `derived_analysis_only` | 解释已冻结结果、bootstrap、归因、综合审计 | 必须引用源结果哈希；只能读取冻结结果；不得重新选样或晋升版本 |
| `feasibility_probe` | 外部数据或覆盖可得性检查 | 明确外部来源；不得声明可交易性、收益或生产可用 |
| `diagnostic_simulation` | 外部数据或自写模拟，仅作诊断/负知识 | 明确 `active_version=null`、不生成订单/纸面目标；不得冒充项目正式回测 |

私有运行资产（`data/strategies/custom/`）属于 `private_runtime`，不进入公共研究证据台账。

## 2. 标准验证套件

新增或字节变化的研究必须按 `docs/superpowers/specs/2026-11-28-standard-research-validation-design.md` 提供 `metrics.standard_validation`：

- `project_formal` / `synthetic_market_stress`：GBM 必须完成至少 100 条路径、4 个情景、2 个相关性和 2x/3x/5x 成本轴，必须有同路径等权基准、无杠杆、确定性重放和至少两张 PNG。
- 参数搜索：必须使用 `train_select_validation_confirm`，训练期选参、验证期确认，近期/测试只披露；必须输出参数路径图、收益-MDD Pareto 图和 bootstrap 图。Bootstrap 必须由选中候选的真实权益曲线做确定性 moving-block 重采样，记录 block size、路径数和 seed；不得用收益/MDD 加减乘除伪造分位区间。
- 图像与 JSON/Markdown 一样必须有 sidecar、进入 ResearchLedger，并使用不可覆盖发布。
- 历史结果按 grandfather hash 保持原样；新结果没有标准验证回执时合同审计 fail-closed。

## 3. 正式回测的硬门禁

`backend/app/factor_research/evidence_guard.py` 的 `require_formal_evidence()` 必须直接跟在 `run_with_evidence()` 之后调用。缺少 `evidence`、持久化未保存、前视检查失败或执行时点检查失败，研究脚本必须抛错终止，不能生成“看似完整”的结论文件。

5x 成本是压力诊断，不是硬否决条件；没有杠杆时，最大敞口不得超过 100%，`active_version` 必须为空。

## 4. 结果、哈希和不可变规则

- 每个研究 JSON/Markdown 都应有同名 `.sha256` sidecar。
- 历史结果不可原地改写。类型修正、口径修正或证据补录必须新增带日期的 amendment / 新版本，并引用原结果哈希。
- 研究台账必须记录 runner、状态、标准类型、处置和文件哈希；审计器不得用未版本化的 LEDGER 覆盖已有台账。
- 删除只适用于可证明无用的临时目录、缓存、重复中间台账；被删除项必须可从审计记录或 Git 历史追溯，不得删除已冻结的正式证据。

## 5. 入口与分类规则

- 新增 `scripts/research_*.py` 前先确定五类研究之一，并在结果中写入 `research_type` 或登记到审计映射。
- 直接调用 `service.run()` 不产生正式证据，不能作为收益结论入口；应改用 `run_with_evidence()` 并调用 guard。
- 间接调用已有正式 helper 时，helper 本身必须包含 guard；审计器只把带 guard 的 helper 回执视为正式链路。
- 自写 `simulate()`、ETF 曲线、overlay 或自定义收益路径，无论是否读取项目数据，都不能自动算作项目正式回测；只能是诊断模拟并明确限制。
- 只读取冻结结果的汇总、归因和综合报告必须引用源哈希，不能重新选择参数或产生新的可晋升结论。

## 6. 审计与未来研究

提交前必须运行：

```bash
python3 scripts/audit_research_conformance.py
python3 scripts/audit_research_research_assets.py
python3 scripts/audit_research_contract.py
cd backend
uv run --frozen pytest tests/test_research_conformance_contract.py tests/test_research_hygiene.py tests/test_research_contract.py -q
cd ..
git diff --check
```

审计结果中不允许出现未分类 runner、缺少 sidecar、`project_chain_without_formal_evidence` 或 `non_project_backtest_or_custom_simulation`。若某项确实只能做诊断，应显式标为 `diagnostic_*` 并保留为负知识，而不是把状态改成正式回测。

研究流程为先写门槛、再跑、再登记台账；过不了的结论留在负知识中，不硬写进交付策略。新增因子必须进入因子库流程，而不是用一次性脚本替代登记、覆盖率和后续可复现性检查。
