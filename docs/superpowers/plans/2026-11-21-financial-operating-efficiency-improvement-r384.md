# R384 财务营运效率改善因子预注册与实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增并审计四个与 R82、R242、R383 不重复的“营运效率改善/相对增长差”财务因子，完成中性化 IC、增量、真实 T+1 无杠杆组合与滚动一年研究，并把正负结论固化为可复现证据。

**Architecture:** 在现有四表 PIT 财务快照中扩展派生列，复用 `attach_fundamental_factors` 和矩阵物化链路；每个因子拥有独立的依赖公告日。研究脚本复用 P5/P8 的统计与组合门禁，冻结 P7/P6 输入，不读取可变 `latest` 污染历史结果。

**Tech Stack:** Python 3.11、Polars、NumPy、pytest、现有 Factor Research P5/P6/P7/P8 研究管线。

**Spec:** `docs/superpowers/specs/2026-11-10-factor-set-research-system-design.md`

## Global Constraints

- 用户不使用杠杆；任何组合回测必须 `leverage=1.0`，不得生成融资仓位。
- 正常成本 `2x` 是硬门槛，严重压力 `3x` 是硬门槛；`5x` 只记录 warning，不得自动否决策略。
- 信号必须真实 T+1 生效，固定持仓数、明确持有期、换手约束和交易成本。
- 单因子通过后仍须通过增量、组合、消融和滚动一年门槛，不能直接生产启用。
- 财务 PIT 公告日当天为 null，严格下一可观测日生效；不补零、不插值、不跨缺口硬拼。
- 历史实验冻结研究 ID；R383 的 P7/P6/P8 输入不得被本轮新增因子改变。
- 本轮只形成 watch 或负知识；`active_version` 保持 null，不产生纸面目标或订单。
- 研究样本为当前 HS300 非银行、非非银金融股票；必须声明当前名单回填历史的幸存者偏差和当前行业快照偏差。
- 本地财务源从 2021Q1 开始；改善因子按实际覆盖从 2022Q4/2023Q1 起算，不得虚构 2018 年覆盖。

---

### Task 1: 冻结 R384 候选与失败边界

**Files:**
- Create: `docs/superpowers/plans/2026-11-21-financial-operating-efficiency-improvement-r384.md`
- Test: `backend/tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py`

**Interfaces:**
- Produces: 四个固定因子 id：`inventory_turnover_improvement_yoy`、`receivable_turnover_improvement_yoy`、`receivable_growth_minus_revenue_growth`、`inventory_growth_minus_cogs_growth`。
- Produces: 固定方向：前两个 `high`，后两个 `low`。
- Produces: 研究主期限 `60` 日，辅助期限 `5/20/60` 日。

- [ ] **Step 1: 写入预注册测试**

```python
def test_r384_pre_registers_only_four_improvement_factors() -> None:
    module = _module()
    assert module.FACTOR_DIRECTIONS == {
        "inventory_turnover_improvement_yoy": "high",
        "receivable_turnover_improvement_yoy": "high",
        "receivable_growth_minus_revenue_growth": "low",
        "inventory_growth_minus_cogs_growth": "low",
    }
    assert {item["factor_id"] for item in module.CANDIDATES} == set(
        module.FACTOR_DIRECTIONS
    )
```

- [ ] **Step 2: 运行预注册测试并确认失败**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py -q`

Expected: FAIL，原因是研究脚本尚不存在。

- [ ] **Step 3: 固定公式**

```text
inventory_turnover_t =
    TTM operating_costs_t / ((inventory_t + inventory_(t-4)) / 2)

receivable_turnover_t =
    TTM revenue_t / ((accounts_receivable_t + accounts_receivable_(t-4)) / 2)

inventory_turnover_improvement_yoy =
    ln(inventory_turnover_t / inventory_turnover_(t-4))

receivable_turnover_improvement_yoy =
    ln(receivable_turnover_t / receivable_turnover_(t-4))

receivable_growth_minus_revenue_growth =
    ln(accounts_receivable_t / accounts_receivable_(t-4))
    - ln(TTM revenue_t / TTM revenue_(t-4))

inventory_growth_minus_cogs_growth =
    ln(inventory_t / inventory_(t-4))
    - ln(TTM operating_costs_t / TTM operating_costs_(t-4))
```

失败边界：任一依赖季度缺失、任一比值分子/分母非正、`t-4` 或 `t-8` 不满足精确四季度间隔时返回 null。改善因子需要 9 个资产负债表季度位置和 8 个连续利润表季度；相对增长差需要当前及一年前资产负债表位置和 8 个连续利润表季度。

- [ ] **Step 4: 冻结查重结论**

```text
R82 已研究纯 inventory_growth_yoy / receivable_growth_yoy，本轮只研究相对营收或营业成本的增速差。
R242 已研究 NWC/总资产的同比变化，本轮不重复 NWC。
R383 已研究 turnover 水平和收益增量，本轮只研究同比改善与相对增长差。
```

- [ ] **Step 5: 提交预注册与失败测试**

```bash
git add docs/superpowers/plans/2026-11-21-financial-operating-efficiency-improvement-r384.md backend/tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py
git commit -m "research: preregister R384 operating efficiency improvement factors"
```

### Task 2: 以 TDD 实现四个 PIT 派生因子

**Files:**
- Modify: `backend/app/backtest/fundamentals.py`
- Modify: `backend/app/factors/registry.py`
- Modify: `backend/app/factor_research/financial_pit.py`
- Modify: `backend/tests/test_fundamental_derived.py`
- Modify: `backend/tests/test_factor_registry.py`

**Interfaces:**
- Consumes: `_quarterly_flow()`、`_ttm_income_revenue`、`_ttm_income_operating_costs`、`_inventory_lag4`、`_receivable_lag4`、`_announce_balance`、`_announce_income`。
- Produces: `DERIVED_FUNDAMENTAL_FACTORS` 中四个新 factor spec，均使用独立 `_announce_*` 列。
- Produces: `BUILTIN_FINANCIAL_DIRECTIONS` 和 taxonomy 中的对应方向。

- [ ] **Step 1: 写数值与 PIT 失败测试**

```python
def test_improvement_factors_require_nine_balance_quarters_and_eight_income_quarters(
    tmp_path: Path,
) -> None:
    _write_tables(
        tmp_path,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    snapshot = load_fundamental_snapshot(tmp_path)
    assert snapshot is not None
    row = snapshot.filter(pl.col("period_end") == date(2024, 3, 31)).row(
        0, named=True
    )
    np.testing.assert_allclose(
        row["inventory_turnover_improvement_yoy"],
        np.log((350.0 / 115.0) / (270.0 / 110.0)),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["receivable_turnover_improvement_yoy"],
        np.log((580.0 / 190.0) / (420.0 / 140.0)),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["receivable_growth_minus_revenue_growth"],
        np.log(220.0 / 160.0) - np.log(580.0 / 420.0),
        rtol=1e-12,
    )
    np.testing.assert_allclose(
        row["inventory_growth_minus_cogs_growth"],
        np.log(130.0 / 100.0) - np.log(350.0 / 270.0),
        rtol=1e-12,
    )
```

- [ ] **Step 2: 写缺口与分母边界失败测试**

```python
def test_improvement_factors_fail_closed_on_gaps_and_nonpositive_inputs(
    tmp_path: Path,
) -> None:
    gap_root = tmp_path / "gap"
    _write_tables(
        gap_root,
        metrics=_improvement_metrics_frame(),
        balance=_improvement_balance_frame(),
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame().filter(
            pl.col("period_end") != "2023-09-30"
        ),
    )
    gap = load_fundamental_snapshot(gap_root)
    assert gap is not None
    gap_row = gap.filter(pl.col("period_end") == date(2024, 3, 31)).row(0, named=True)
    assert gap_row["inventory_turnover_improvement_yoy"] is None
    assert gap_row["receivable_turnover_improvement_yoy"] is None
    assert gap_row["receivable_growth_minus_revenue_growth"] is None
    assert gap_row["inventory_growth_minus_cogs_growth"] is None

    zero_root = tmp_path / "zero"
    balance = _improvement_balance_frame().with_columns(
        pl.when(pl.col("period_end") == "2024-03-31")
        .then(pl.lit(0.0))
        .otherwise(pl.col("inventory"))
        .alias("inventory")
    )
    _write_tables(
        zero_root,
        metrics=_improvement_metrics_frame(),
        balance=balance,
        cash=_improvement_cash_frame(),
        income=_improvement_income_frame(),
    )
    zero = load_fundamental_snapshot(zero_root)
    assert zero is not None
    zero_row = zero.filter(pl.col("period_end") == date(2024, 3, 31)).row(0, named=True)
    assert zero_row["inventory_turnover_improvement_yoy"] is None
    assert zero_row["inventory_growth_minus_cogs_growth"] is None
    assert zero_row["receivable_turnover_improvement_yoy"] is not None
    assert zero_row["receivable_growth_minus_revenue_growth"] is not None
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `cd backend && uv run --frozen pytest tests/test_fundamental_derived.py -q -k 'improvement'`

Expected: FAIL，输出缺少新因子列。

- [ ] **Step 4: 最小实现派生公式与独立公告日**

在 `_derive_ttm_factors()` 中先计算四个基础改善列，再用这些列与四季度前值计算最终因子：

```python
ttm_revenue_lag4 = pl.col("_ttm_income_revenue").shift(4).over("symbol")
ttm_costs_lag4 = pl.col(
    "_ttm_income_operating_costs"
).shift(4).over("symbol")
inventory_turnover_lag4 = pl.col("inventory_turnover_ttm").shift(4).over("symbol")
receivable_turnover_lag4 = pl.col(
    "receivable_turnover_ttm"
).shift(4).over("symbol")
```

公告日使用当前及 `t-4` 资产负债表公告日；改善因子再加入 `t-8` 资产负债表公告日；全部因子取 `t-7` 到 `t` 八个利润表公告日的最大值。最终列和 `_announce_*` 列均加入 `keep`。

- [ ] **Step 5: 运行定向测试**

Run: `cd backend && uv run --frozen pytest tests/test_fundamental_derived.py -q -k 'improvement or operating_efficiency or legacy'`

Expected: PASS。

- [ ] **Step 6: 注册四个财务因子并更新 PIT 方向测试**

在 `_CATALOG` 末尾追加四个 `_financial(...)`，不重排既有因子。更新 `test_factor_registry.py` 的黄金数量为 87 个 stock / 52 virtual / 70 ETF，并断言四个新因子均为 stock-only PIT：

```python
for factor_id in (
    "inventory_turnover_improvement_yoy",
    "receivable_turnover_improvement_yoy",
    "receivable_growth_minus_revenue_growth",
    "inventory_growth_minus_cogs_growth",
):
    spec = get_factor(factor_id)
    assert spec is not None
    assert spec.pit is True
    assert spec.pit_source == "financial_announce"
    assert spec.asset_types == frozenset({"stock"})
```

- [ ] **Step 7: 运行注册表与矩阵测试**

Run: `cd backend && uv run --frozen pytest tests/test_factor_registry.py tests/test_fundamental_derived.py -q`

Expected: PASS。

- [ ] **Step 8: 提交因子实现**

```bash
git add backend/app/backtest/fundamentals.py backend/app/factors/registry.py backend/app/factor_research/financial_pit.py backend/tests/test_fundamental_derived.py backend/tests/test_factor_registry.py
git commit -m "feat: add PIT operating efficiency improvement factors"
```

### Task 3: 刷新 P7、taxonomy、catalog 与 P6 准入

**Files:**
- Modify: `scripts/sync_factor_taxonomy.py`
- Modify: `backend/tests/factor_research/test_financial_pit_p7_scan.py`
- Modify: `backend/tests/factor_research/test_parameters.py`
- Modify: `backend/tests/test_factor_store.py`
- Generated: `data/factor_research/pit/runs/*`
- Generated: `data/factor_research/catalog/field_taxonomy.json`
- Generated: `data/factor_research/catalog/snapshots/*`
- Generated: `data/factor_research/automation/runs/*`
- Generated: `data/factor_research/ledger/*`

**Interfaces:**
- Consumes: 四个注册且可计算的 stock-only PIT 财务因子。
- Produces: 新 P7 `research_id`，21 个 verified 财务因子。
- Produces: 新 catalog `factor_count=229`，新 P6 admission 包含四个因子且 `portfolio_ready=true`。
- Produces: R384 冻结使用的新 P7/P6 research ID。

- [ ] **Step 1: 写黄金快照预期**

```python
assert len(payload["verified_factor_ids"]) == 21
assert len(catalog.factors) == 229
assert len(factor_columns_view()) == 87
```

- [ ] **Step 2: 运行 P7、taxonomy、catalog**

```bash
cd backend
uv run --frozen python ../scripts/audit_financial_pit_p7.py
uv run --frozen python ../scripts/sync_factor_taxonomy.py
uv run --frozen python ../scripts/sync_factor_research_catalog.py
```

Expected: P7 `status=verified`、21/21；taxonomy 四个新因子 `pit_hint=verified`、`future_data_status=clear`；catalog `factor_count=229`。

- [ ] **Step 3: 运行 P6 并验证四个新因子准入**

```bash
cd backend
uv run --frozen python ../scripts/research_factor_automation_p6.py
uv run --frozen python - <<'PY'
import json
from pathlib import Path
root = Path("../data/factor_research/automation")
latest = json.loads((root / "latest.json").read_text())
payload = json.loads((Path("..") / "data" / latest["run_json"]).read_text())
for factor_id in (
    "inventory_turnover_improvement_yoy",
    "receivable_turnover_improvement_yoy",
    "receivable_growth_minus_revenue_growth",
    "inventory_growth_minus_cogs_growth",
):
    row = payload["admission"][factor_id]
    assert row["portfolio_ready"] is True, (factor_id, row)
print(latest["research_id"])
PY
```

Expected: 输出新的 P6 research ID，`portfolio_ready_factor_count=170`。

- [ ] **Step 4: 冻结 R384 的 P7/P6 ID**

把上两步输出的 P7、P6 `research_id` 写入 R384 研究脚本常量；测试必须断言 R384 输出引用的正是这两个不可变 run。

- [ ] **Step 5: 运行研究基础设施测试**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_pit_p7_scan.py tests/factor_research/test_automation_p6_scan.py tests/factor_research/test_parameters.py tests/test_factor_store.py -q`

Expected: PASS。

- [ ] **Step 6: 提交准入刷新**

```bash
git add scripts/sync_factor_taxonomy.py backend/tests/factor_research/test_financial_pit_p7_scan.py backend/tests/factor_research/test_parameters.py backend/tests/test_factor_store.py data/factor_research
git commit -m "research: admit R384 improvement factors through P7 and P6"
```

### Task 4: 发布 R384 中性化、增量与组合研究

**Files:**
- Create: `scripts/research_financial_operating_efficiency_improvement_r384.py`
- Create: `data/factor_research/research/r384_financial_operating_efficiency_improvement_2026-11-21.json`
- Create: `data/factor_research/research/r384_financial_operating_efficiency_improvement_2026-11-21.md`
- Modify: `backend/tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py`

**Interfaces:**
- Consumes: 冻结 P7/P6 research ID；现有 `multi_dimension_v1` P5/P6 基准；真实 T+1 系统回测。
- Produces: 每个因子的 raw/industry/size/industry+size 5/20/60 日 rank IC。
- Produces: 60 日 80/20 增量 IC、NW p、BH q、最大相关、组合、消融和滚动一年结果。
- Produces: ledger record `financial_operating_efficiency_improvement_scan_published`。

- [ ] **Step 1: 实现研究脚本的最小结构**

```python
FACTOR_DIRECTIONS = {
    "inventory_turnover_improvement_yoy": "high",
    "receivable_turnover_improvement_yoy": "high",
    "receivable_growth_minus_revenue_growth": "low",
    "inventory_growth_minus_cogs_growth": "low",
}
PRIMARY_HORIZON = 60
CANDIDATES = tuple(
    {
        "candidate_key": f"r384_{factor_id}",
        "economic_key": factor_id,
        "label": factor_id,
        "factor_id": factor_id,
        "direction": direction,
        "registered": True,
        "transform": "pit_latest",
        "set_role": "new",
        "replaces_factor_id": None,
    }
    for factor_id, direction in FACTOR_DIRECTIONS.items()
)
```

- [ ] **Step 2: 复用但冻结现有门禁**

必须复制 R383 已经验证的 `load_p7_audit`、`load_research_panel`、`neutralized_ic_grid`、`factor_horizon_gate`、`candidate_status`、`_portfolio_stage` 和 ledger 发布逻辑，只替换 factor 集合、输出路径、operation、method version 与 P7/P6 ID。不得读取 `pit/latest.json` 或 `automation/latest.json` 代替冻结 ID。

门禁：

```text
期限：60 日方向调整后至少两个中性化口径 IC>0 且 NW p<=0.10。
增量：方向调整后 60 日边际 IC>0，p<=0.10 且 BH q<=0.10。
冗余：平均截面绝对相关<0.90；0.70~0.90 记录研究警告。
组合：真实 T+1、无杠杆、固定持仓、明确持有期；2x/3x 为硬门槛。
消融：移除新因子后基准不得改善；滚动一年必须同向通过。
5x：只写 warning，不计入否决。
```

- [ ] **Step 3: 先统计预览，不发布**

Run: `cd backend && uv run --frozen python ../scripts/research_financial_operating_efficiency_improvement_r384.py --stats-only`

Expected: 输出四个因子 coverage、IC grid、期限门槛、增量门槛；此时不得写 ledger。

- [ ] **Step 4: 发布完整研究并验证幂等**

Run: `cd backend && uv run --frozen python ../scripts/research_financial_operating_efficiency_improvement_r384.py`

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py -q`

Expected: canonical research ID 可复算；重复运行不改变 JSON、Markdown、ledger head 或 records；`active_version=null`、`orders_generated=false`、`production_enabled=false`。

- [ ] **Step 5: 根据结果作唯一允许的决策**

```text
没有任何期限通过：no_horizon_candidate。
有期限通过但无增量通过：no_incremental_candidate。
增量和组合通过但消融/滚动失败：对应 no_*_candidate。
全部通过：watch_candidate_passed_no_promotion，仍不生产启用。
```

不得在看到结果后修改公式、方向、主期限或门槛；若需探索参数，另立研究轮次。

- [ ] **Step 6: 提交研究报告**

```bash
git add scripts/research_financial_operating_efficiency_improvement_r384.py backend/tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py data/factor_research/research/r384_financial_operating_efficiency_improvement_2026-11-21.json data/factor_research/research/r384_financial_operating_efficiency_improvement_2026-11-21.md data/factor_research/ledger
git commit -m "research: evaluate R384 operating efficiency improvement factors"
```

### Task 5: 全量验证、审计与推送

**Files:**
- Review: 最终 diff 全部文件
- Verify: backend tests、ruff、frontend build、CI

**Interfaces:**
- Consumes: Task 1-4 全部产物。
- Produces: 可复现的本地验证结果、干净工作区和远端 main。

- [ ] **Step 1: 运行定向与全量验证**

```bash
cd backend
uv run --frozen pytest tests/test_fundamental_derived.py tests/test_factor_registry.py tests/factor_research/test_financial_pit_p7_scan.py tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py tests/factor_research/test_automation_p6_scan.py -q
uv run --frozen pytest tests -q
uv run --frozen ruff check app/backtest/fundamentals.py app/factors/registry.py app/factor_research/financial_pit.py tests/test_fundamental_derived.py tests/test_factor_registry.py tests/factor_research/test_financial_operating_efficiency_improvement_r384_scan.py tests/factor_research/test_financial_pit_p7_scan.py
cd ../frontend
pnpm build
cd ..
git diff --check
git status --short --branch
```

Expected: 全部测试/构建通过；仅允许既有 Vite chunk warning。

- [ ] **Step 2: 检查最终 diff 和冻结关系**

确认 R383 的 P7/P6 research ID 与 evidence 文件未改变；R384 只引用新增 P7/P6；没有未提交生成物；没有密钥、本机路径或临时文件。

- [ ] **Step 3: 推送并观察 CI**

```bash
git push origin main
gh run list --branch main --limit 4
```

Expected: CI 与 Docker 构建成功；若失败，先定位真实根因并修复，不能只重跑掩盖。

- [ ] **Step 4: 汇报量化结果和剩余风险**

最终说明必须包含：四个因子 coverage、主要 IC、增量结论、是否进入组合、2x/3x 结果、5x warning、最终决策、P7/P6 research ID、提交 SHA、CI 状态和幸存者偏差风险。

## Self-Review

- 公式覆盖：四个候选分别对应两个 turnover 改善和两个相对增长差，未复用 R82/R242/R383 的纯增长、NWC 或 turnover 水平。
- PIT 覆盖：每个因子都验证 8/9 季度依赖、非正分母、缺口、公告日 T+1、旧快照兼容、面板/矩阵一致。
- 门禁覆盖：期限、增量、相关、组合、消融、滚动一年均有任务。
- 无杠杆与成本：2x/3x 硬门槛，5x 仅 warning，符合用户口径。
- 可复现性：R383 冻结不被污染；R384 使用新 P7/P6 不可变 research ID；幂等测试检查 ledger 与报告。
