# 扩展数据窗口因子参数筛选 P3 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将 P2 后剩余的 53 个未分类扩展/自定义窗口因子全部归入 `grouped`、`fixed` 或 `blocked`，并对 PIT 与覆盖率达标的 23 个参数族完成股票截面与市场 conditioner 两条参数研究链。

**Architecture:** 先用注册表显式记录 fixed/blocked 原因并消除所有 `unclassified`；再新增纯函数评估器，分别覆盖事件线性衰减、行业窗口和市场时间序列窗口；最后复用 P1/P2 的 Monte Carlo、FDR、研究台账和不可变证据协议跑 P3。市场级因子只输出 conditioner 候选，任何结果都不进入股票 alpha 腿或 active 版本。

**Tech Stack:** Python 3.11、Pydantic v2、Polars、NumPy、pytest、ResearchLedger、JSON/Markdown evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

> **Execution note (2026-11-11):** P3 已完成。首轮 v1 结果将稀疏事件只保留事件行，导致
> 董监高与分析师评级变化无法形成有效横截面；该结果保留为不可变失败证据，并由零填充后的
> v2 结果取代。随后复核发现分析师评级调整在研报日上重复加 1 天，实际延后到 T+2；修正后
> 重建扩展因子快照并生成 v3，P3 奇偶校验通过。最终验收以
> `p3_extended_parameter_mc_2026-11-11_v3.*` 为准。

## Global Constraints

- 训练段 `2021-06-01..2024-12-31`，检验段 `2025-01-01..end`，近一年 `2025-10-14..end`。
- 5 日 forward return，最小横截面 30，初筛 1000 次，决赛 5000 次，主 block 20，敏感性 block 10/20/40。
- 参数网格预先固定；不做结果驱动的补点。
- `future_data_status=unknown`、PIT 不可追溯、数据陈旧或定义来源不可提交的因子不得调参。
- 市场级因子只能进入 conditioner 候选，不得写成股票 alpha，不得自动进入 watch 或 active。
- 所有研究产物只写仓库相对路径；重复运行不得覆盖历史结果或重复追加台账。
- `multi_dimension_v1`、`core_v1` 保持 watch，`active_version` 保持 null。
- 不修改用户已有的 R359-R371、custom factors/signals、`.gitignore` 和周报工作。

---

### Task 1: 扩展注册表分类原因并清空 53 个未分类项

**Files:**
- Modify: `backend/app/factor_research/parameters.py`
- Modify: `backend/tests/factor_research/test_parameters.py`
- Modify: `data/factor_research/parameters/families.json`

**Interfaces:**
- Produces: `ParameterFamilyRegistry.fixed_factor_reasons: dict[str, str]`
- Produces: `ParameterFamilyRegistry.blocked_factor_reasons: dict[str, str]`
- Produces: P3 23 个可调族及 27 个 fixed/blocked 明确原因。
- Consumes: 现有 `FactorRecord.future_data_status`、`pit_status` 和 catalog 223 因子。

**Family mapping:**

| family_id | grid | current | members |
| --- | --- | ---: | --- |
| `industry_momentum` | 10,20,40,60,120 | 20 | `industry_mom_20d`, `industry_mom_60d` |
| `industry_rps` | 10,20,40,60 | 20 | `industry_rps_20d` |
| `industry_breadth` | 10,20,40,60 | 20 | `industry_breadth_20d` |
| `stock_rel_industry_strength` | 10,20,40,60 | 20 | `stock_rel_industry_20d` |
| `insider_buy_event` | 5,10,20,40,60 | 20 | `insider_buy_20d` |
| `insider_net_buy_event` | 5,10,20,40,60 | 20 | `insider_net_buy_20d` |
| `insider_sell_event_low` | 5,10,20,40,60 | 20 | `insider_sell_20d_low` |
| `lhb_net_buy_event` | 5,10,20,40,60 | 20 | `lhb_net_buy_ratio_20d` |
| `lhb_inst_net_buy_event` | 5,10,20,40,60 | 20 | `lhb_inst_net_buy_ratio_20d` |
| `lhb_event_count_low` | 5,10,20,40,60 | 20 | `lhb_event_count_20d_low` |
| `repurchase_event_decay` | 20,40,60,120 | 60 | `repurchase_event_60d` |
| `block_trade_ratio_event` | 5,10,20,40,60 | 20 | `block_trade_ratio_20d_low` |
| `block_premium_event` | 5,10,20,40,60 | 20 | `block_premium_20d` |
| `analyst_rating_level` | 5,10,20,40,60 | 20 | `analyst_rating_score_20d` |
| `analyst_rating_change` | 5,10,20,40,60 | 20 | `analyst_rating_change_20d` |
| `bond_futures_momentum` | 5,10,20,40,60 | 20 | `bond_mom_20d` |
| `bond_futures_trend_momentum` | 5,10,20,40,60 | 20 | `bond_tf_mom_20d` |
| `hsi_momentum` | 5,10,20,40,60 | 20 | `hsi_mom_20d` |
| `hscei_momentum` | 5,10,20,40,60 | 20 | `hsciei_mom_20d` |
| `hsi_relative_hs300` | 5,10,20,40,60 | 20 | `hsi_rel_hs300_20d` |
| `etf_share_change` | 1,2,3,6 | 1 | `etf_share_chg_1m`, `etf_share_chg_3m` |
| `qvix_zscore` | 60,120,252 | 252 | `qvix_z_252` |
| `market_skew` | 20,40,60,120 | 60 | `market_skew_60d_low` |

All IDs above are prefixed by `ext_alt_hs300_` in the registry. Stock/event families use `neutralization=["industry","size"]`; industry-replicated families use `["size"]`; market families use `[]` and `cross_section_type="market_level"`.

**Blocked IDs with explicit reasons:**

- `cash_flow_stability_4q_low`, `gross_margin_stability_4q_low`, `management_expense_ratio_4q_low`, `net_financing_4q_low`, `ocf_margin_4q`, `sga_ratio_4q_industry_neutral`, `sga_ratio_4q_low`, `sga_ratio_4q_qoq_improvement`, `sga_ratio_stability_4q_low`, `equity_supply_ratio_180d_low`: `future_data_status=unknown` until field-level PIT is promoted by a separate audit.
- `forecast_surprise_60d`, `express_surprise_60d`: `future_data_status=unknown`; not tuned despite announcement-date source code until catalog audit is promoted.
- `northbound_net_buy_20d`: last non-null date `2024-08-16`; stale and not eligible for current research.
- `uf_industry_relative_strength_change_5d`: custom definition has `pit_status=unknown`, `future_data_status=unknown`, and its source file is uncommitted user data.

**Fixed IDs with explicit reasons:**

- `ah_premium_chg_1m`, `if_net_short_chg_1m`, `margin_safety_chg_20d`, `nh_nl_diff_20d`, `market_breadth_above_ma20`, `hs300_turnover_chg_20d`, `hs300_turnover_chg_20d_ext2017`, `shibor_3m`, `shibor_3m_chg_20d`, `m1_m2_scissors_chg_1m`: fixed market/conditioner semantics with only one audited source horizon.
- `holder_net_buy_ratio_90d`, `holder_net_buy_count_90d`, `margin_chg_1m_low`, `pledge_ratio_chg_1m_low`: fixed structural lookbacks whose source tables expose one audited horizon; threshold work belongs to P4.

- [x] **Step 1: Write failing registry tests**

```python
def test_registry_rejects_reason_for_unknown_factor() -> None:
    registry = ParameterFamilyRegistry.model_validate({
        "schema_version": 1,
        "families": [],
        "fixed_factor_reasons": {"missing": "fixed"},
    })
    errors = validate_parameter_registry(registry, {"f1"})
    assert errors == ["missing factor missing in fixed_factor_reasons"]


def test_classification_uses_explicit_blocked_reason() -> None:
    rows = classify_factor_parameters(factors, registry)
    assert by_id["ext_alt_hs300_northbound_net_buy_20d"].status == "blocked"
    assert by_id["ext_alt_hs300_northbound_net_buy_20d"].reason == "stale: last non-null 2024-08-16"
```

- [x] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py -q`

Expected: FAIL because `fixed_factor_reasons` and `blocked_factor_reasons` are not defined.

- [x] **Step 3: Implement strict reason maps**

`ParameterFamilyRegistry` gains two default-empty dictionaries. Validation rejects reason keys outside their corresponding ID list. `classify_factor_parameters` uses the explicit reason when a factor is fixed or blocked, otherwise keeps the existing generic reason.

- [x] **Step 4: Add the P3 families and explicit statuses**

Add the family table above and both status maps to `families.json`. Keep P1/P2 families unchanged.

- [x] **Step 5: Rebuild the P3 classification report and verify zero unclassified**

Run:

```bash
cd backend
uv run --frozen python ../scripts/build_factor_parameter_registry.py \
  --output ../data/factor_research/parameters/reports/classification_p3_2026-11-11.json
```

Expected: 223 total, `unclassified=0`; P3 contributes 23 tunable families and 25 grouped factors; every P3 fixed/blocked row carries an explicit reason.

### Task 2: 新增事件、行业和市场窗口纯函数评估器

**Files:**
- Create: `backend/app/factor_research/extended_parameter_evaluators.py`
- Create: `backend/tests/factor_research/test_extended_parameter_evaluators.py`

**Interfaces:**
- Produces: `ExtendedEvaluator = Callable[[Mapping[str, Any], int], tuple[pl.DataFrame, str, str]]`
- Produces: `EXTENDED_EVALUATORS: dict[str, ExtendedEvaluator]`
- Produces: `event_decay_factor(events, calendar, value_column, window, output_column, multiplier=1.0) -> pl.DataFrame`
- Produces: `industry_window_frame(prices, kind, window) -> tuple[pl.DataFrame, str, str]`
- Produces: `market_momentum_frame(series, window, output_column) -> tuple[pl.DataFrame, str, str]`
- Produces: `market_relative_momentum_frame(left, benchmark, window, output_column)`
- Produces: `market_change_frame(series, window, value_column, output_column, multiplier=1.0)`
- Produces: `rolling_zscore_frame(series, window, value_column, output_column, multiplier=1.0)`
- Produces: `market_skew_frame(series, window, output_column)`
- Produces: `rank_score_series(frame, factor_column, return_column, sign) -> pl.DataFrame`
- Produces: 23 evaluator names matching Task 1 family `evaluator` values.

**Event decay contract:** an event dated `D` starts at the first trading day strictly after `D`, contributes `value * (1 - offset/window)` for offsets `0..window-1`, and zero/no-event rows remain null. Multiple events on the same symbol/date are summed before decay.

**Industry contract:** use only `close`, `date`, `symbol`, `industry_l1`; momentum is `close / close.shift(window) - 1`; breadth is the share of industry members with `close > rolling_mean(close, window)`; RPS is the ordinal cross-industry rank of momentum divided by industry count; stock relative strength is stock momentum minus equal-weight industry momentum.

**Market contract:** market momentum and relative momentum use close-to-close windows; ETF share change uses `shares / shares.shift(months) - 1`; QVIX z-score uses rolling mean/std; market skew returns negative third-moment skew of daily returns.

- [x] **Step 1: Write failing event-decay tests**

```python
def test_event_decay_starts_next_trading_day_and_sums_events() -> None:
    calendar = pl.DataFrame({
        "symbol": ["A"] * 4,
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
    })
    events = pl.DataFrame({
        "symbol": ["A", "A"],
        "date": [date(2024, 1, 2), date(2024, 1, 2)],
        "value": [1.0, 2.0],
    })
    out = event_decay_factor(events, calendar, "value", 3, "factor_value")
    assert out["factor_value"].to_list() == [3.0, 2.0, 1.0]
```

- [x] **Step 2: Run test and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_extended_parameter_evaluators.py::test_event_decay_starts_next_trading_day_and_sums_events -q`

Expected: collection fails because `extended_parameter_evaluators` does not exist.

- [x] **Step 3: Implement event decay with explicit calendar indexing**

Attach `trade_index` per symbol, map each event to the first calendar date strictly after its announcement using `join_asof(strategy="forward")`, expand only `window` offsets, sum weighted values by `(symbol, trade_index)`, then join back to the calendar. Raise on missing keys, nonpositive windows, or unsorted/duplicate calendar keys.

- [x] **Step 4: Write failing industry and market tests**

```python
def test_industry_momentum_rps_and_relative_strength_are_distinct() -> None:
    prices = synthetic_two_industry_prices()
    momentum, mom_col, _ = industry_window_frame(prices, "momentum", 2)
    rps, rps_col, _ = industry_window_frame(prices, "rps", 2)
    relative, rel_col, _ = industry_window_frame(prices, "stock_relative", 2)
    latest = max(prices["date"])
    assert momentum.filter(pl.col("date") == latest)[mom_col].n_unique() == 2
    assert rps.filter(pl.col("date") == latest)[rps_col].n_unique() == 2
    assert relative.filter(pl.col("date") == latest)[rel_col].n_unique() == 4


def test_qvix_zscore_matches_rolling_formula() -> None:
    series = pl.DataFrame({"date": calendar(5), "close": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out, col, formula = rolling_zscore_frame(series, 3, "close", "factor_value")
    assert out[col].to_list()[-1] == pytest.approx(1.0)
    assert formula == "zscore(close, 3)"
```

- [x] **Step 5: Implement all 23 evaluators and formula templates**

Register names exactly as Task 1: `industry_momentum`, `industry_rps`, `industry_breadth`, `stock_rel_industry_strength`, `insider_buy_event`, `insider_net_buy_event`, `insider_sell_event_low`, `lhb_net_buy_event`, `lhb_inst_net_buy_event`, `lhb_event_count_low`, `repurchase_event_decay`, `block_trade_ratio_event`, `block_premium_event`, `analyst_rating_level`, `analyst_rating_change`, `bond_futures_momentum`, `bond_futures_trend_momentum`, `hsi_momentum`, `hscei_momentum`, `hsi_relative_hs300`, `etf_share_change`, `qvix_zscore`, `market_skew`.

- [x] **Step 6: Run focused evaluator tests and Ruff**

Run:

```bash
cd backend
uv run --frozen pytest tests/factor_research/test_extended_parameter_evaluators.py -q
uv run --frozen ruff check app/factor_research/extended_parameter_evaluators.py \
  tests/factor_research/test_extended_parameter_evaluators.py
```

Expected: PASS.

### Task 3: 加载 P3 原始数据并校验当前窗口逐值对齐

**Files:**
- Create: `backend/app/factor_research/extended_parameter_data.py`
- Create: `backend/tests/factor_research/test_extended_parameter_data.py`

**Interfaces:**
- Produces: `load_event_panels(data_dir: Path, symbols: list[str]) -> dict[str, pl.DataFrame]`
- Produces: `load_market_series(data_dir: Path) -> dict[str, pl.DataFrame]`
- Produces: `load_existing_ext_factors(data_dir: Path, factor_fields: Mapping[str, str]) -> pl.DataFrame`
- Produces: `load_existing_market_factors(data_dir: Path, factor_fields: Mapping[str, str]) -> pl.DataFrame`
- Produces: `compare_factor_frames(generated, existing, value_column, *, tolerance=1e-10) -> dict[str, Any]`

**Event panels:**

- `insider_events`: `symbol,date,buy,sell` from `data/research/r90_insider_trade/*.parquet`; unique symbol/day direction flags, matching the existing builder.
- `lhb_events`: `symbol,date,net_buy_ratio,inst_net_buy_ratio,event_count` from `data/research/r43_lhb/detail_*.parquet` and `jgmm_*.parquet`.
- `repurchase_events`: `symbol,date,event` from `data/research/external_r39/repurchase.parquet`.
- `block_events`: `symbol,date,block_ratio,premium` from `data/research/external_r39/dzjy_*.parquet`.
- `analyst_reports`: `symbol,date,rating,rating_change` from `data/research/r47_analyst/*.parquet`.

**Market series:** `T0`, `TF0`, `HSI`, `HSCEI`, `etf_510300_shares`, `qvix_300etf`; each panel has `date` plus either `close` or `value` and is deduplicated by date.

- [x] **Step 1: Write failing loader tests with synthetic caches**

```python
def test_lhb_loader_builds_signed_ratio_institution_ratio_and_count(tmp_path: Path) -> None:
    write_synthetic_lhb_cache(tmp_path)
    panels = load_event_panels(tmp_path, ["000001.SZ"])
    row = panels["lhb_events"].row(0, named=True)
    assert row["net_buy_ratio"] == pytest.approx(0.5)
    assert row["inst_net_buy_ratio"] == pytest.approx(0.2)
    assert row["event_count"] == 2
```

- [x] **Step 2: Run test and verify loader module is missing**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_extended_parameter_data.py::test_lhb_loader_builds_signed_ratio_institution_ratio_and_count -q`

Expected: collection failure for missing module.

- [x] **Step 3: Implement normalized loaders and fail-closed schemas**

Every loader validates required columns, casts dates and numerics strictly enough to surface source drift, filters to current symbols, and raises `FileNotFoundError` when a required cache family is absent. Loader outputs never include machine-absolute paths.

- [x] **Step 4: Write failing current-window parity tests**

```python
@pytest.mark.parametrize("family_id", P3_CURRENT_PARITY_FAMILIES)
def test_current_p3_variant_matches_existing_ext_factor(family_id: str) -> None:
    result = compare_one_current_variant(family_id)
    assert result["status"] == "pass"
    assert result["max_abs_error"] <= 1e-10
```

- [x] **Step 5: Implement parity comparison and enforce it before scanning**

Compare only common `(symbol,date)` keys, require at least 95% key overlap for stock factors and 95% date overlap for market factors, and fail on either value drift above tolerance or unexplained null coverage drift above 1 percentage point.

- [x] **Step 6: Run focused data tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_extended_parameter_data.py -q`

Expected: PASS, with parity checks using the local research caches; CI uses synthetic fixtures and skips only the local-cache parity integration case when the ignored research data is absent.

### Task 4: 实现 P3 股票截面与市场 conditioner 扫描

**Files:**
- Create: `scripts/research_extended_parameter_families.py`
- Create: `backend/tests/factor_research/test_extended_parameter_scan.py`

**Interfaces:**
- Produces: `load_p3_families(data_dir: Path) -> list[ParameterFamily]`
- Produces: `p3_research_registry_payload(families) -> dict[str, Any]`
- Produces: `scan_stock_family(family, context, research_id, start, end) -> dict[str, Any]`
- Produces: `scan_market_family(family, context, research_id, start, end) -> dict[str, Any]`
- Produces: `primary_block_ci(trial) -> float | None`
- Produces: `market_initial_eligibility(trial) -> bool`
- Produces: `render_markdown(payload) -> str`
- Consumes: Task 1 registry, Task 2 evaluators, Task 3 loaders, P1/P2 `parameter_mc` helpers.

**Stock gate:** identical to P1/P2, except neutralization is selected from each family: `["industry","size"]` for stock/event families, `["size"]` for industry-replicated families. The gate IC is the combined neutralization actually declared by the family.

**Market gate:** use `time_series_rank_regression` for full/train/test/recent; directional beta is `beta * sign`. A 1000/5000 moving-block bootstrap is applied to the signed daily rank-covariance score series. Initial eligibility requires robust plateau, full/train/test directional beta positive, family BH-FDR `q <= 0.10`, and bootstrap CI lower bound positive. Final recommendation additionally requires primary block-20 final CI positive and uses `min(train_beta, test_beta)`, final CI, then distance to current parameter.

- [x] **Step 1: Write failing stock-family scan tests**

```python
def test_stock_family_scan_rejects_single_point_and_emits_all_trials() -> None:
    result = scan_stock_family(synthetic_family(grid=[10, 20]), synthetic_context(), "sha256:test", START, END)
    assert result["recommendation_status"] == "rejected"
    assert [trial["parameter"] for trial in result["trials"]] == [10, 20]
    assert all(trial["plateau_status"] == "single_point_unverified" for trial in result["trials"])
```

- [x] **Step 2: Write failing market-conditioner tests**

```python
def test_market_family_is_conditioner_only_and_requires_fdr() -> None:
    result = scan_market_family(positive_market_family(), synthetic_market_context(), "sha256:test", START, END)
    assert result["role"] == "conditioner"
    assert result["stock_alpha_eligible"] is False
    assert result["recommendation_status"] == "recommended"
    assert result["recommended_trial"]["q_value"] <= 0.10
```

- [x] **Step 3: Run tests and verify scan functions are missing**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_extended_parameter_scan.py -q`

Expected: FAIL with missing `scan_stock_family` / `scan_market_family`.

- [x] **Step 4: Implement stock scan using shared P1/P2 statistics**

For each parameter, evaluate the factor, join to price/forward-return rows, calculate raw and declared neutralized IC, initial bootstrap, coverage, train/test/recent/yearly statistics, plateau and family FDR. Finalists rerun 5000 replications for blocks 10/20/40 with deterministic seeds.

- [x] **Step 5: Implement market scan and conditioner-only boundaries**

For each parameter, join factor dates to HS300 forward returns, compute time-series rank regression statistics, family FDR, signed rank-score bootstrap and plateau. Emit `role="conditioner"`, `stock_alpha_eligible=false`, and `use="market-regime conditioner research only"`.

- [x] **Step 6: Implement CLI publication, idempotence and ledger binding**

Default output is `data/factor_research/research/parameters/p3_extended_parameter_mc_2026-11-11.json`; Markdown uses the same stem. Research ID binds method version, catalog snapshot, P3 registry payload, P1/P2 research IDs, date range, bootstrap settings and input-file manifest. Existing files are accepted only when `research_id` and rendered Markdown match byte-for-byte; ledger append is skipped for an existing research ID.

- [x] **Step 7: Run focused scan tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_extended_parameter_scan.py -q`

Expected: PASS.

### Task 5: 运行 P3、生成证据并做全链路验收

**Files:**
- Output: `data/factor_research/parameters/reports/classification_p3_2026-11-11.json`
- Output: `data/factor_research/parameters/reports/classification_p3_2026-11-11.md`
- Output: `data/factor_research/research/parameters/p3_extended_parameter_mc_2026-11-11_v2.json`
- Output: `data/factor_research/research/parameters/p3_extended_parameter_mc_2026-11-11_v2.md`
- Superseded output: `data/factor_research/research/parameters/p3_extended_parameter_mc_2026-11-11.*`
- Modify: `data/factor_research/ledger/head.json`
- Modify: `data/factor_research/ledger/records.jsonl`

- [x] **Step 1: Run the P3 classification build**

Run:

```bash
cd backend
uv run --frozen python ../scripts/build_factor_parameter_registry.py \
  --output data/factor_research/parameters/reports/classification_p3_2026-11-11.json
```

Expected: `unclassified=0`; P3 counts are 25 grouped, 14 fixed, 14 blocked among the former 53.

- [x] **Step 2: Run all current-window parity checks**

Run:

```bash
cd backend
uv run --frozen python ../scripts/research_extended_parameter_families.py --parity-only
```

Expected: 25 current parameter variants pass against the existing extension table within `1e-10`; any unexplained null-coverage drift fails closed.

- [x] **Step 3: Run the full P3 Monte Carlo**

Run:

```bash
cd backend
uv run --frozen python ../scripts/research_extended_parameter_families.py \
  --output data/factor_research/research/parameters/p3_extended_parameter_mc_2026-11-11_v2.json
```

Expected: 23 families, 107 parameter trials, every family `recommended` or `rejected`; market results are conditioner-only and rejected when FDR or CI fails.

- [x] **Step 4: Verify idempotence and ResearchLedger closure**

Re-run Step 3. Expected: identical `research_id`, JSON SHA and Markdown SHA; ledger record count unchanged. Verify the complete ledger hash chain, every evidence SHA sidecar, and `active_version=null` for `multi_dimension_v1` and `core_v1`.

- [x] **Step 5: Run focused and full regression tests**

Run:

```bash
cd backend
uv run --frozen pytest tests/factor_research/test_parameters.py \
  tests/factor_research/test_extended_parameter_evaluators.py \
  tests/factor_research/test_extended_parameter_data.py \
  tests/factor_research/test_extended_parameter_scan.py -q
uv run --frozen pytest tests -q
uv run --frozen ruff check app/factor_research/extended_parameter_evaluators.py \
  app/factor_research/extended_parameter_data.py \
  tests/factor_research/test_extended_parameter_evaluators.py \
  tests/factor_research/test_extended_parameter_data.py \
  tests/factor_research/test_extended_parameter_scan.py \
  ../scripts/research_extended_parameter_families.py
```

- [x] **Step 6: Verify clean CI-equivalent tracked tree**

Create a temporary `git archive HEAD` tree, apply the P3 diff, run the full backend suite under `linux/amd64` Python 3.12 with `uv sync --extra dev --frozen`, and require zero failures before committing.

- [x] **Step 7: Exact-stage, commit and push**

Stage only Task 1-5 files and immutable evidence. Do not use `git add -A`. Commit message: `feat: add extended factor parameter research`. Push `main`, then require both GitHub CI and Docker workflows to succeed.

## Self-Review

- Spec coverage: P3 event windows, industry factors, market conditioner boundaries, PIT/coverage checks, FDR, Monte Carlo, ledger and immutable evidence are covered.
- Classification coverage: the 53 P2 unclassified factors map to 25 grouped, 14 fixed and 14 blocked; no `unclassified` remains.
- Fail-closed: future-data unknown, stale northbound, unknown custom definitions and non-audited horizons never enter tuning.
- Type consistency: Task 1 evaluator names match Task 2 registry, Task 3 context keys feed Task 4 scans, and Task 4 output schema is consumed by Task 5 publication checks.
- No active promotion: P3 can at most emit conditioner or parameter-stable research records; P5 remains the only place that may propose a new watch set.
