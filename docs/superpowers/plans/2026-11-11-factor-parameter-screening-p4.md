# 阈值型与事件后持有期参数筛选 P4 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 为股息率、基金拥挤、融资变化、股东结构和事件后持有期建立预注册阈值研究链，完成绝对阈值/截面分位两种口径、两阶段 Monte Carlo、样本外结论和不可变证据。

**Architecture:** 新建独立于窗口参数族的阈值注册表，复用 P1-P3 的 Rank IC、中性化、BH-FDR、block bootstrap、推荐排序和 ResearchLedger；阈值只生成研究变体，不修改注册因子定义，不自动进入 watch 或 active。

**Tech Stack:** Python 3.11、Pydantic v2、Polars、NumPy、pytest、ResearchLedger、JSON/Markdown evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

> **Execution note (2026-11-11):** P4 已完成。最终跑批包含 23 个参数族、190 个
> 参数点、16 个初筛入围和 4 个推荐参数。分析师评级调整的研报日生效口径修正后，
> P3 已重建扩展因子快照并发布 v3；P4 基于 P3 v3 重跑。最终 research_id 为
> `sha256:128842a1c50fe850fd961e4cc46cafd8a1cfe372f5f6bbcc81fbe3171f66c588`，
> JSON/Markdown SHA 分别为
> `sha256:9c65600b73b19cab7d89c76a7b83ef416cdc3125dadc7ce61e0d47353e63f1d4` 和
> `sha256:309f075699d39e29ae985bc40ad6e16455aac1020c388f93811cddc9e10d3d46`；
> 幂等重跑保持 research_id、JSON/Markdown SHA 和 ledger 记录数不变。

## Global Constraints

- 训练段 `2021-06-01..2024-12-31`，检验段 `2025-01-01..end`，近一年 `2025-10-14..end`。
- 5 日 forward return，最小横截面 30，初筛 1000 次，决赛 5000 次，主 block 20，敏感性 block 10/20/40。
- 阈值网格和事件持有期在跑批前写入注册表；不做结果驱动补点。
- 绝对阈值和分位数阈值分别成族，不能把不同口径混在同一 BH-FDR 家族。
- 阈值选择不得用组合累计收益；推荐排序只使用训练/检验 IC、决赛 CI 和到参考参数的距离。
- 单一或极窄平台必须标记为不稳定并拒绝。
- 事件型因子必须使用公告日之后的首个交易日生效；未知 PIT、陈旧数据和未审计字段不得调参。
- 所有研究产物只写仓库相对路径；重复运行不得覆盖历史结果或重复追加台账。
- `multi_dimension_v1`、`core_v1` 保持 watch，`active_version` 保持 null。

## Pre-registered Families

### Threshold families

| source_factor_id | absolute grid | reference |
| --- | --- | ---: |
| `ext_alt_hs300_dividend_yield` | 0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.06 | 0.02 |
| `ext_alt_hs300_fund_count_low` | -8, -7, -6, -5, -4, -3, -2 | -5 |
| `ext_alt_hs300_fund_count_chg_qoq_low` | -0.5, -0.2, 0, 0.2, 0.5, 0.7, 0.9 | 0.2 |
| `ext_alt_hs300_fund_holding_ratio_low` | -0.20, -0.15, -0.10, -0.075, -0.05, -0.03, -0.02, -0.01 | -0.05 |
| `ext_alt_hs300_fund_holding_ratio_chg_qoq_low` | -3, -2, -1, -0.5, 0, 0.5, 0.8 | -0.5 |
| `ext_alt_hs300_margin_chg_1m_low` | -0.3, -0.2, -0.1, 0, 0.05, 0.1, 0.2 | 0 |
| `ext_alt_hs300_holder_net_buy_ratio_90d` | -3, -1, 0, 0.5, 1, 2, 5 | 0.5 |
| `ext_alt_hs300_holder_net_buy_count_90d` | -2, 0, 1, 2, 3, 5, 10 | 2 |
| `ext_alt_hs300_pledge_ratio_chg_1m_low` | -1, -0.5, 0, 0.2, 0.5, 1, 2 | 0.2 |

All threshold sources use direction `high` because the registered extension field is already direction-adjusted. Each source also receives a quantile family with grid `0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95` and reference `0.50`. Quantile `q` for a high-direction factor means pass when `factor >= daily_q`.

### Event holding families

| family | source factor | event | grid | reference |
| --- | --- | --- | --- | ---: |
| `insider_buy_holding` | `ext_alt_hs300_insider_buy_20d` | 董监高增持 | 5, 10, 20, 40, 60 | 20 |
| `lhb_net_buy_holding` | `ext_alt_hs300_lhb_net_buy_ratio_20d` | 龙虎榜净买入 | 5, 10, 20, 40, 60 | 20 |
| `repurchase_holding` | `ext_alt_hs300_repurchase_event_60d` | 回购公告 | 5, 10, 20, 40, 60 | 20 |
| `block_premium_holding` | `ext_alt_hs300_block_premium_20d` | 大宗交易溢价 | 5, 10, 20, 40, 60 | 20 |
| `analyst_rating_change_holding` | `ext_alt_hs300_analyst_rating_change_20d` | 分析师评级调整 | 5, 10, 20, 40, 60 | 20 |

事件信号在公告日后的首个交易日生效，并在固定持有期内保持常数；同一天多个事件先求和。

---

### Task 1: 阈值注册表与严格校验

**Files:**
- Create: `backend/app/factor_research/threshold_parameters.py`
- Create: `data/factor_research/parameters/thresholds.json`
- Create: `backend/tests/factor_research/test_threshold_parameters.py`

**Interfaces:**
- Produces: `ThresholdFamily`, `ThresholdRegistry`, `load_threshold_registry(data_dir)`.
- Produces: `validate_threshold_registry(registry, catalog, window_registry) -> list[str]`.
- Threshold families require `source_factor_id`, `mode`, and a nonempty grid; event families require `event_key` and positive integer holding days.
- The registry rejects duplicate family IDs, duplicate `(source_factor_id, mode)` pairs, duplicate event families, invalid quantiles, missing source factors, and source factors classified as `unclassified`.

- [x] **Step 1: Write failing registry tests for valid coverage and duplicate/invalid grids**
- [x] **Step 2: Run focused tests and verify the module is missing**
- [x] **Step 3: Implement the models, loader and validation**
- [x] **Step 4: Add the pre-registered 23 families**
- [x] **Step 5: Run registry tests and Ruff**

### Task 2: 绝对阈值、分位阈值和事件持有期纯函数

**Files:**
- Create: `backend/app/factor_research/threshold_evaluators.py`
- Modify: `backend/app/factor_research/extended_parameter_evaluators.py`
- Create: `backend/tests/factor_research/test_threshold_evaluators.py`
- Modify: `backend/tests/factor_research/test_extended_parameter_evaluators.py`

**Interfaces:**
- Produces: `threshold_indicator(frame, parameter, mode, direction, value_column="factor_value") -> pl.DataFrame`.
- Produces: `event_holding_factor(events, calendar, value_column, holding_days, output_column, multiplier=1.0, fill_zero=True) -> pl.DataFrame`.
- `event_decay_factor` gains an optional `decay: Literal["linear", "step"] = "linear"` without changing linear behavior.
- Produces: `EVENT_HOLDING_EVALUATORS` for the five event families.

- [x] **Step 1: Write failing absolute and daily-quantile threshold tests**
- [x] **Step 2: Write failing event-holding step tests covering next-trading-day, isolation and zero fill**
- [x] **Step 3: Run focused tests and verify failures**
- [x] **Step 4: Implement the evaluators while preserving P3 parity**
- [x] **Step 5: Run focused evaluator tests and Ruff**

### Task 3: P4 扫描、结论和证据发布

**Files:**
- Create: `scripts/research_threshold_parameter_families.py`
- Create: `backend/tests/factor_research/test_threshold_parameter_scan.py`
- Output: `data/factor_research/research/parameters/p4_threshold_parameter_mc_2026-11-11.json`
- Output: `data/factor_research/research/parameters/p4_threshold_parameter_mc_2026-11-11.md`

**Interfaces:**
- Produces: `load_p4_families(data_dir) -> list[ThresholdFamily]`.
- Produces: `scan_threshold_family(family, context, factor_panel, research_id, start, end) -> dict`.
- Produces: `scan_event_holding_family(family, context, research_id, start, end) -> dict`.
- Produces: `render_markdown(payload) -> str` and deterministic CLI publication.
- Every trial records threshold mode, source factor, pass coverage, raw IC, declared industry+size neutral IC, train/test/recent statistics, BH-FDR `q`, initial bootstrap and final block sensitivity.
- A recommendation requires robust plateau, full/train/test IC positive, declared neutral IC positive, family FDR `q <= 0.10`, initial CI positive and final block-20 CI positive.

- [x] **Step 1: Write failing threshold-family scan tests**
- [x] **Step 2: Write failing event-holding scan and conditioner-boundary tests**
- [x] **Step 3: Implement the shared stock-signal scan and P4 publication**
- [x] **Step 4: Run focused P4 tests**
- [x] **Step 5: Run full P4 with 23 families and 190 pre-registered trials**
- [x] **Step 6: Re-run and verify identical research ID, hashes and ledger count**

### Task 4: 全链路验收与提交

**Files:** only Task 1-3 files and immutable evidence.

- [x] **Step 1: Verify all fixed threshold sources remain classified, no unclassified factor appears, and no active/watch version changes**
- [x] **Step 2: Run focused parameter/evaluator/scan tests and full backend suite**
- [x] **Step 3: Run Ruff, `git diff --check`, frontend build and clean linux/amd64 Python 3.12 CI-equivalent suite**
- [x] **Step 4: Stage only P4 files, commit and push without touching pre-existing R359-R371/custom/weekly-report work**
- [x] **Step 5: Require GitHub CI and Docker workflows to succeed**

## Self-Review

- Spec coverage: dividend yield, fund crowding, margin change, shareholder structure, absolute/quantile thresholds and event holding periods are covered.
- Fail-closed: invalid grids, duplicate modes, unknown factors, stale/PIT-unknown sources and single-point/narrow platforms cannot be recommended.
- No active promotion: P4 only publishes parameter-stable stock candidates for P5 incremental testing.
