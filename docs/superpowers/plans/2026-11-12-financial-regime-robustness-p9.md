# 财务因子期限分层与 Regime 条件化 P9 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 P8 已证明“5 日直接替换无效”之后，检验 20/60 日期限分层和 T-1 弱市 regime 条件化是否能形成同时改善全周期、近一年和弱市段的无杠杆组合。

**Architecture:** 复用 P8 的 PIT 财务面板和 P5 的 T+1 矩阵回测；新增的只是期限收益、行业/规模中性化、T-1 regime mask 和预先声明的条件腿，不复制回测引擎。任何新腿先做期限 IC、相关性和邻域稳定性，再进入组合；最终仍只允许 watch。

**Tech Stack:** Python 3.11、Polars、现有 `neutralize_scoring_columns`、`build_regime_filter_mask`/T-1 对齐逻辑、P5 prepared matrix、pytest、ResearchLedger。

**Spec:** `docs/superpowers/plans/2026-11-12-financial-factor-increment-p8.md`

## Global Constraints

- 不使用杠杆；2x 正常、3x 硬压力、5x 仅 warning。
- 财务因子只允许 P7 `verified_factor_ids`；P6 `portfolio_ready` 是第二道 fail-closed。
- regime 对齐必须使用 T-1，禁止用当日尚未可知的情绪状态。
- 任何候选项都必须在全周期、近一年、弱市一年、3x、滚动一年和 ablation 上同时成立。
- 变异族必须预注册，不能根据结果追加窗口；显著性和稳定性门槛保持 P5：p/q≤0.10、最大相关≤0.90、正收益窗口不低基线、中位收益不差超过 1pp。
- HS300 当前成分回填历史的幸存者偏差必须在报告中保留。
- `active_version=null`；不生成纸面目标、订单或生产启用。
- 先写失败测试，再实现；只精确暂存本任务文件，禁止 `git add -A`。

---

### Task 1: 预注册期限和条件腿

**Files:**
- Test: `backend/tests/factor_research/test_financial_regime_p9_scan.py`
- Create: `scripts/research_financial_regime_p9.py`

**Interfaces:**
- Produces: `FORWARD_HORIZONS = (5, 20, 60)`
- Produces: `REGIME_HYPOTHESES`（仅 `pb_latest`、`accrual`、`ocf_ni` 三个 P8 诊断中同号的财务腿）
- Produces: `regime_mask_t_minus_one(panel_dates, regime_history) -> pl.DataFrame`

- [ ] **Step 1: 写失败测试**

```python
def test_regime_mask_uses_previous_trading_state():
    dates = pl.DataFrame({"date": ["2026-01-05", "2026-01-06", "2026-01-07"]})
    history = pl.DataFrame({
        "date": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "state": ["strong", "weak", "weak"],
    })
    mask = module.regime_mask_t_minus_one(dates, history)
    assert mask["is_weak"].to_list() == [False, False, True]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_regime_p9_scan.py -q`
Expected: FAIL because P9 module is absent.

- [ ] **Step 3: 实现 T-1 mask 和固定假设**

只登记三个财务腿和三个 horizon；不根据本轮结果增加新因子。弱市状态仅使用 `state=="weak"`，之前交易日缺失时 fail-closed。

- [ ] **Step 4: 跑测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_regime_p9_scan.py -q`
Expected: PASS。

---

### Task 2: 期限 IC 和中性化诊断

**Files:**
- Modify: `scripts/research_financial_regime_p9.py`
- Test: `backend/tests/factor_research/test_financial_regime_p9_scan.py`

**Interfaces:**
- Produces: `build_horizon_panel(panel: pl.DataFrame, horizons=FORWARD_HORIZONS) -> pl.DataFrame`
- Produces: `neutralized_ic_grid(panel, factor_ids, horizons, variants) -> dict`
- Consumes: `neutralize_scoring_columns`、`calculate_rank_ic`、`stats_v2.newey_west_t`。

- [ ] **Step 1: 写失败测试**

```python
def test_horizon_panel_adds_forward_returns_without_lookahead():
    panel = module.build_horizon_panel(fixture, horizons=(20, 60))
    assert panel["forward_return_20d"].is_not_null().sum() > 0
    assert panel.filter(pl.col("date") == panel["date"].max())["forward_return_20d"].null_count() == panel.height
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_regime_p9_scan.py -q`
Expected: FAIL。

- [ ] **Step 3: 实现诊断**

同一因子、同一 horizon 下比较 raw/industry/size/industry+size 四种口径；IC、NW t、样本天数、覆盖率全部落盘。只有至少两个中性化口径同号且 p≤0.10 的腿才允许进入组合预检。

---

### Task 3: 条件化组合与稳定性

**Files:**
- Modify: `scripts/research_financial_regime_p9.py`
- Test: `backend/tests/factor_research/test_financial_regime_p9_scan.py`

**Interfaces:**
- Produces: `build_conditional_factor(panel, factor_id, regime_mask, state="weak", neutral_fill="median") -> pl.DataFrame`
- Produces: `weight_neighbourhood(center: float, steps=(0.05, 0.10, 0.15, 0.20, 0.30)) -> list[float]`
- Produces: `run_p9_portfolio(...) -> dict`

- [ ] **Step 1: 写失败测试**

```python
def test_conditional_factor_is_constant_outside_weak_state():
    out = module.build_conditional_factor(fixture, "pb_latest", mask, state="weak")
    nonweak = out.filter(~pl.col("is_weak"))
    assert nonweak["pb_latest_conditional"].n_unique() <= 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_regime_p9_scan.py -q`
Expected: FAIL。

- [ ] **Step 3: 实现组合和邻域**

候选必须经过：P5 边际 IC/相关性、全周期和近一年 2x、全周期 3x、弱市 2x、ablation、滚动一年、权重邻域。中心权重必须处于至少三个相邻权重同向的平台；单点最优不算通过。

---

### Task 4: 证据发布与 P6 接线

**Files:**
- Create: `data/factor_research/research/p9_financial_regime_2026-11-12.json|md`
- Modify: `scripts/research_factor_automation_p6.py`
- Modify: `backend/tests/factor_research/test_automation_p6_scan.py`
- Modify: `data/factor_research/ledger/*`

- [ ] **Step 1: 写失败测试**

```python
def test_p9_output_keeps_active_version_null():
    payload = json.loads(P9_RESULT.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_regime_p9_scan.py tests/factor_research/test_automation_p6_scan.py -q`
Expected: FAIL。

- [ ] **Step 3: 发布并重跑 P6**

P9 Research ID 绑定 P7/P8/P5/P6 catalog 指纹和预注册假设；同输入重跑不得改变 JSON/Markdown/Ledger。P6 增加 P9 binding，并保持 `portfolio_ready_factor_count=164`、`active_version=null`。

---

### Task 5: 验收、提交与 CI

- [ ] 运行 `cd backend && uv run --frozen pytest tests -q`
- [ ] 运行 `cd backend && uv run --frozen ruff check ../scripts/research_financial_regime_p9.py tests/factor_research/test_financial_regime_p9_scan.py`
- [ ] 运行 `git diff --check`
- [ ] 精确暂存并提交 `feat: add financial regime robustness P9`
- [ ] 推送并等待 GitHub CI/Docker 成功

任何候选未通过全周期、近一年、弱市段、3x、ablation 或滚动一年中的一项，都记录负知识，不硬塞进 watch。
