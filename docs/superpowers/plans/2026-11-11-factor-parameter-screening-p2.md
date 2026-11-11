# 内置窗口因子参数筛选 P2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将内置动量、均线、波动率、布林带、价格位置、流动性和涨停窗口因子纳入统一参数族，完成两阶段蒙特卡洛、P1 跨族相关性矩阵和 fail-closed 分类。

**Architecture:** 先把 P1 已有的平台、初筛、决赛、推荐排序抽到共享 `parameter_mc` 模块，保证 P1 产物与行为不变；再用可单测的 Polars evaluator 精确重算内置窗口因子；最后以注册表驱动 P2 跑批并生成不可变 JSON/Markdown/台账证据。

**Tech Stack:** Python 3.11、Pydantic v2、Polars、NumPy、pytest、ResearchLedger、markdown/JSON evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

## Global Constraints

- 训练段 `2021-06-01..2024-12-31`，检验段 `2025-01-01..end`，近一年 `2025-10-14..end`。
- 5 日 forward return，最小横截面 30，初筛 1000 次，决赛 5000 次，主 block 20，敏感性 block 10/20/40。
- 参数网格预先固定；不做结果驱动的补点。
- evaluator 当前窗口输出必须与已有 enriched 因子公式逐值对齐；不相同的因子不得进入推荐。
- 同一经济含义和方向才能属于同一参数族；多窗口复合指标显式进入 `fixed`，非 scale-free 的原始 ATR 进入 `blocked`。
- 所有研究产物只写仓库相对路径；重复运行不得覆盖历史结果或重复追加台账。
- 不产生 active 版本、订单或纸面目标。

---

### Task 1: 抽取共享蒙特卡洛引擎并保持 P1 零回归

**Files:**
- Create: `backend/app/factor_research/parameter_mc.py`
- Modify: `scripts/research_alt_factor_parameters.py`
- Create: `backend/tests/factor_research/test_parameter_mc.py`
- Modify: `backend/tests/factor_research/test_parameter_scan.py`

**Interfaces:**
- Produces:
  - `stats_for_period(ic, start, end, sign, *, include_distribution=False) -> dict`
  - `bootstrap_ic(ic, start, end, sign, *, block_size, n_bootstrap, seed) -> dict`
  - `bootstrap_seed(research_id, family_id, parameter, block_size, n_bootstrap) -> int`
  - `annotate_plateau(trials) -> None`
  - `select_robust_candidates(trials) -> list[dict]`
  - `choose_recommended_candidate(trials, *, current_parameter) -> dict | None`
  - `initial_failure_counts(trials) -> dict[str, int]`
  - `assign_family_q_values(trials) -> None`
  - `final_ci_low(trial) -> float | None`
- P1 script imports and reuses those names; behavior, `research_id`, trial schema and existing P1 evidence remain unchanged.
- Tests cover plateau edges, positive train/test/CI gates, final CI rejection and recommendation tie-breaking.

- [x] **Step 1: Move the existing generic tests to import `app.factor_research.parameter_mc`**
- [x] **Step 2: Run focused tests and verify the new module import fails**
- [x] **Step 3: Extract the current P1 implementations without changing formulas or constants**
- [x] **Step 4: Re-run P1 tests and the idempotent P1 script; ledger sequence and output SHA must remain unchanged**
- [x] **Step 5: Run Ruff for the new module, P1 script and tests**

### Task 2: 扩展参数注册表并增加内置窗口 evaluator

**Files:**
- Modify: `backend/app/factor_research/parameters.py`
- Modify: `backend/tests/factor_research/test_parameters.py`
- Create: `backend/app/factor_research/builtin_parameter_evaluators.py`
- Create: `backend/tests/factor_research/test_builtin_parameter_evaluators.py`

**Interfaces:**
- `ParameterFamilyRegistry` adds backward-compatible `fixed_factor_ids: list[str] = []`.
- `ParameterFamily` adds optional `formula_template: str | None = None`.
- Explicit fixed factors override numeric-window detection; overlaps remain validation errors.
- `BUILTIN_EVALUATORS: dict[str, Callable[[pl.DataFrame, int], tuple[pl.DataFrame, str, str]]]` returns `(frame, value_column, formula_text)`.
- Evaluators cover momentum, MA/EMA bias, RSI, annual/downside volatility, ATR%, Bollinger position/width, price position, distance to high/low, Amihud, amount/turnover level, turnover stability/crowding, volume/turnover/amount spike, OBV, volume-price correlation, limit-up count, max return, skew, up days, overnight and intraday return.
- Every current-window evaluator is parity-tested against the canonical enriched formula.

- [x] **Step 1: Write failing registry tests for explicit fixed factors and overlap rejection**
- [x] **Step 2: Write failing synthetic and canonical-parity evaluator tests**
- [x] **Step 3: Implement fixed-factor classification and optional formula metadata**
- [x] **Step 4: Implement vectorized evaluator registry and formula identity**
- [x] **Step 5: Run `tests/factor_research/test_parameters.py tests/factor_research/test_builtin_parameter_evaluators.py -q`**

### Task 3: 扩展 P2 参数族并生成新分类报告

**Files:**
- Modify: `data/factor_research/parameters/families.json`
- Modify: `backend/tests/factor_research/test_parameters.py`
- Output: `data/factor_research/parameters/reports/classification_p2_2026-11-11.json`
- Output: `data/factor_research/parameters/reports/classification_p2_2026-11-11.md`

**P2 families:**

| family | evaluator | direction | grid | current |
| --- | --- | --- | --- | ---: |
| short_momentum_reversal | momentum | low | 3,5,10,15,20 | 5 |
| medium_momentum | momentum | high | 20,30,60,120 | 20 |
| short_ma_reversal | ma_bias | low | 3,5,10,15,20 | 5 |
| medium_ma_trend | ma_bias | high | 20,30,60,120 | 20 |
| short_ema_reversal | ema_bias | low | 3,5,10,15,20 | 5 |
| medium_ema_trend | ema_bias | high | 20,30,60,120 | 20 |
| rsi_reversal | rsi | low | 6,14,24,40,60 | 14 |
| annual_volatility | annual_volatility | low | 10,20,40,60,120 | 20 |
| downside_volatility | downside_volatility | low | 10,20,40,60,120 | 20 |
| atr_risk | atr_pct | low | 5,10,14,20,60 | 14 |
| bollinger_position | bollinger_position | high | 10,20,40,60 | 20 |
| bollinger_width | bollinger_width | low | 10,20,40,60 | 20 |
| price_position | price_position | high | 60,120,240 | 240 |
| distance_to_high | distance_to_high | high | 20,60,120,240 | 240 |
| distance_from_low | distance_from_low | high | 20,60,120,240 | 60 |
| amihud | amihud | high | 5,10,20,40,60,120 | 20 |
| amount_level | amount_level | low | 5,10,20,40,60,120 | 20 |
| turnover_level | turnover_level | low | 5,10,20,40,60,120 | 20 |
| turnover_stability | turnover_stability | low | 5,10,20,40,60,120 | 20 |
| turnover_crowding | turnover_crowding | low | 10,20,60,120,240 | 60 |
| volume_spike | volume_ratio | low | 5,10,20,60 | 5 |
| turnover_spike | turnover_ratio | low | 5,10,20,60 | 5 |
| amount_spike | amount_ratio | low | 5,10,20,60 | 5 |
| obv_trend | obv_trend | high | 5,10,20,40,60 | 20 |
| volume_price_corr | volume_price_corr | high | 5,10,20,40,60 | 20 |
| limit_up_gene | limit_up_count | high | 5,10,20,40,60,120 | 20 |
| max_return | max_return | low | 5,10,20,60 | 20 |
| return_skew | return_skew | high | 5,10,20,60 | 20 |
| up_day_ratio | up_day_count | high | 5,10,20,60 | 20 |
| overnight_return | overnight_return | high | 5,10,20,60 | 20 |
| intraday_return | intraday_return | low | 5,10,20,60 | 20 |

- [x] **Step 1: Add a failing test that every built-in numeric window factor is either grouped or explicitly fixed/blocked**
- [x] **Step 2: Add the 31 P2 families and explicit fixed/blocked IDs to `families.json`**
- [x] **Step 3: Update P1 family loading to select only P1 evaluators instead of assuming every tunable family is P1**
- [x] **Step 4: Run the registry builder into the P2 report path**
- [x] **Step 5: Assert zero built-in `unclassified` factors and ResearchLedger SHA closure**

### Task 4: 跑 P2 参数蒙特卡洛并生成跨 P1 相关性矩阵

**Files:**
- Create: `scripts/research_builtin_parameter_families.py`
- Create: `backend/tests/factor_research/test_builtin_parameter_scan.py`
- Output: `data/factor_research/research/parameters/p2_builtin_parameter_mc_2026-11-11_v2.json`
- Output: `data/factor_research/research/parameters/p2_builtin_parameter_mc_2026-11-11_v2.md`

**Interfaces:**
- `load_p2_families(data_dir) -> list[ParameterFamily]` selects only registered builtin evaluators.
- `build_variant_payload(...)` produces the same trial schema as P1 plus `formula_text`, `variant_id`, `source_factor_id`.
- `cross_family_correlation_matrix(...)` returns average Spearman, average absolute Spearman and maximum absolute correlation for each P2/P1 family pair.
- Recommended parameter requires initial robustness, 5000-run block-20 `ci_low > 0`; block 10/40 are sensitivity evidence.
- P1 comparison uses recommended parameter when present, otherwise current parameter, and records that basis.

- [x] **Step 1: Write failing tests for P2 family selection, variant IDs and correlation sign/maximum**
- [x] **Step 2: Implement registry-driven P2 scan reusing `parameter_mc`**
- [x] **Step 3: Add deterministic correlation sampling across common dates and symbols**
- [x] **Step 4: Run P2: expected 31 families and 143 parameter trials**
- [x] **Step 5: Verify all families have `recommended` or `rejected`, all recommendations have final evidence, and no absolute paths exist**

### Task 5: 全链路验证、提交和 CI

**Files:** only Task 1-4 files and immutable evidence.

- [x] **Step 1: Run focused P1/P2 tests**
- [x] **Step 2: Re-run P1 and P2 scripts; ledger count and research IDs must remain unchanged**
- [x] **Step 3: Verify ResearchLedger hash chain, output/markdown SHA and watch-set invariants**
- [x] **Step 4: Run full backend suite, Ruff and `git diff --check`**
- [x] **Step 5: Stage exact P2 files, commit and push without touching pre-existing R359-R371/custom/weekly-report work**
- [x] **Step 6: Wait for GitHub CI and Docker workflows to succeed**

## Self-Review

- Spec coverage: P2 parameter families, formulas, recommendation, correlation matrix, fixed/blocked explanations and immutability are covered.
- Fail-closed: every built-in window factor must be grouped, fixed, or blocked; none may remain unclassified.
- Type consistency: shared `parameter_mc` functions are consumed unchanged by P1 and P2; evaluator output schema is identical across all built-in families.
