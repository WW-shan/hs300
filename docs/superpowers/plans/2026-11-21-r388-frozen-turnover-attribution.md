# R388 冻结策略换手归因 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不调整任何策略参数的前提下，量化 R386/R387 冻结候选的股票换手来源、成本拖累和样本稳定性，为后续是否值得研究低换手替代方案提供证据。

**Architecture:** 新增一个独立、append-only 的研究 runner。它复用 R387 已冻结的 A/B/C 回测曲线和逐笔交易缓存，按 sleeve 与日期归因交易金额，并从 R387 报告读取冻结参数、成本场景和输入哈希；不重新生成策略曲线，不读取 11 天后的数据，不生成订单或生产配置。

**Tech Stack:** Python 3.11、Polars、pytest、Ruff、R387 evidence JSON/Markdown。

**Spec:** `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r387_preregistration.json`

## Global Constraints

- R386 参数必须保持 `c_satellite_cap=0.13`、`equity_target_vol=0.17`、`overlay_lookback_days=110`、`overlay_target_vol=0.17`、`overlay_equity_weight_max=0.37`。
- 不修改 A/B/C 策略代码、组合权重、再平衡规则、持有期、止损、TopN、行业映射或成本口径。
- 研究窗口固定为 `2022-05-05` 至 `2026-11-20`；R387 新观察窗口固定为 `2026-11-06` 至 `2026-11-20`。
- 2x 是主成本口径，3x 是压力口径；5x 只记录 warning，不能作为硬否决。
- 不使用杠杆；所有报告的暴露必须 `<= 1.0`。
- 不读取未来数据，不把 R387 的 11 天表现用于重新选参。
- 输出 append-only；已存在的 JSON/Markdown/sidecar 必须拒绝覆盖。
- 不生成订单、纸面目标或生产配置；成功状态只能是 `attribution_complete_no_decision`。

## Task 1: 预登记与失败测试

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_preregistration.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_preregistration.json.sha256`
- Create: `backend/tests/test_r388_frozen_turnover_attribution.py`

**Interfaces:**
- Consumes: `r387_forward_shadow_confirmation.json` and its sidecar.
- Produces: frozen parameters, input hashes, date windows, cost protocol, output policy and success status.

- [ ] **Step 1: 写预登记 JSON**

必须写入以下关键字段：

```json
{
  "round": "R388",
  "classification": "frozen-strategy turnover and cost attribution; no tuning, no execution",
  "window": {
    "full_start": "2022-05-05",
    "full_end": "2026-11-20",
    "shadow_start": "2026-11-06",
    "shadow_end": "2026-11-20"
  },
  "freeze": {
    "source_round": "R387",
    "parameter_search_allowed": false,
    "strategy_change_allowed": false,
    "parameters": {
      "c_satellite_cap": 0.13,
      "equity_target_vol": 0.17,
      "overlay_lookback_days": 110,
      "overlay_target_vol": 0.17,
      "overlay_equity_weight_max": 0.37
    }
  },
  "cost_protocol": {
    "main": "2x",
    "stress": "3x",
    "warning_only": "5x",
    "five_x_can_hard_reject": false
  },
  "output_policy": {
    "status": "attribution_complete_no_decision",
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

- [ ] **Step 2: 写失败测试**

测试必须覆盖：

```python
def test_r388_freezes_parameters_and_has_no_tuning_path():
    payload = json.loads(PREREG.read_text())
    assert payload["round"] == "R388"
    assert payload["freeze"]["parameter_search_allowed"] is False
    assert payload["freeze"]["strategy_change_allowed"] is False
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["output_policy"]["status"] == "attribution_complete_no_decision"
```

并断言 sidecar SHA256 匹配、R387 runner/JSON/MD 的输入哈希存在于预登记。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
cd backend
uv run pytest tests/test_r388_frozen_turnover_attribution.py -q
```

Expected: FAIL，因为 runner/预登记产物尚未齐备。

- [ ] **Step 4: 生成 sidecar**

```bash
shasum -a 256 r388_turnover_attribution_preregistration.json > r388_turnover_attribution_preregistration.json.sha256
```

- [ ] **Step 5: 提交预登记**

```bash
git add data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_preregistration.json \
  data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_preregistration.json.sha256 \
  backend/tests/test_r388_frozen_turnover_attribution.py
git commit -m "research: preregister R388 frozen turnover attribution"
```

## Task 2: 实现只读归因 runner

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r388_frozen_turnover_attribution.py`
- Modify: `backend/tests/test_r388_frozen_turnover_attribution.py`

**Interfaces:**
- Consumes: `r387_forward_shadow_confirmation.json`, its `.sha256`, R387 runner, R387 result Markdown, and the R387 cache manifest.
- Produces: `attribute_costs`, `summarize_sleeves`, `build_report`, `markdown`.

- [ ] **Step 1: 定义只读输入契约**

runner 必须：

- 校验预登记 sidecar；
- 校验 R387 JSON/Markdown/runner 的 SHA256；
- 读取 R387 `parameters`、`input_sha256`、`costs`、`benchmark`、`window`、`decision`；
- 拒绝 R387 `decision.status != "shadow_observation_complete_no_decision"`；
- 拒绝 R387 `parameters != EXPECTED_PARAMETERS`；
- 拒绝 R387 `no_leverage != true`；
- 拒绝 R387 `window.end != 2026-11-20`。

- [ ] **Step 2: 定义归因公式**

对每个成本场景 `c`：

```text
buy_notional_c = sum(A/B/C buy_notional)
sell_notional_c = sum(A/B/C sell_notional)
one_way_turnover_c = (buy_notional_c + sell_notional_c) / (2 * initial_capital)
round_trip_cost_bps_c = commission_bps + stamp_tax_bps + slippage_bps
estimated_cost_c = (buy_notional_c + sell_notional_c) / initial_capital * round_trip_cost_bps_c / 2
```

`initial_capital` 固定为 `1_000_000`。归因只做成本近似，不声称与引擎逐笔费用完全等价。

- [ ] **Step 3: 输出 sleeve 明细**

报告必须包含每个 sleeve 的：

```text
sleeve
buy_notional
sell_notional
one_way_turnover
share_of_total_turnover
entry_count
exit_count
full_trade_count
```

A/B/C 的金额必须来自 R387 `costs[c].trades`，不得用报告中的组合总金额重复分摊。

- [ ] **Step 4: 输出成本与稳定性**

报告必须包含：

- 2x/3x/5x 总收益、Sharpe、MDD、换手、估算成本；
- 2x sleeve 换手占比；
- R387 11 天窗口的收益和换手；
- R387 全历史指标；
- 一个明确 warning 列表：换手是否超过预登记阈值、5x 是否仅 warning、是否存在 sleeve 集中度 warning。

预登记换手 warning 阈值固定为 `one_way_turnover >= 5.0`；这不是硬否决。

- [ ] **Step 5: 输出 append-only**

runner 必须拒绝覆盖：

```text
r388_turnover_attribution.json
r388_turnover_attribution.json.sha256
r388_turnover_attribution_result.md
r388_turnover_attribution_result.md.sha256
```

- [ ] **Step 6: 写测试**

新增测试必须覆盖：

```python
def test_cost_attribution_uses_summed_sleeve_notional():
    ...
    assert result["estimated_cost_2x"] == pytest.approx(expected)


def test_runner_rejects_stale_r387_window():
    ...


def test_runner_refuses_existing_output():
    ...


def test_five_x_is_warning_only():
    ...
```

- [ ] **Step 7: 运行定向测试**

Run:

```bash
cd backend
uv run pytest tests/test_r388_frozen_turnover_attribution.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交 runner 与测试**

```bash
git add data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r388_frozen_turnover_attribution.py \
  backend/tests/test_r388_frozen_turnover_attribution.py
git commit -m "research: add R388 frozen turnover attribution runner"
```

## Task 3: 生成并审计 R388 证据

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution.json.sha256`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_result.md`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_result.md.sha256`

**Interfaces:**
- Consumes: Task 2 runner and R387 evidence.
- Produces: final R388 JSON/Markdown and sidecars.

- [ ] **Step 1: 运行 runner**

```bash
cd backend
uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r388_frozen_turnover_attribution.py
```

Expected: 输出 2x 主口径、3x 压力、5x warning、sleeve 换手占比和 `attribution_complete_no_decision`。

- [ ] **Step 2: 人工审计关键数字**

必须确认：

- R387 输入哈希与 sidecar 一致；
- 2x/3x/5x 曲线收益与 R387 完全一致；
- 三个 sleeve 的 buy/sell notional 与 R387 对应场景一致；
- 2x 换手 warning 为 `true`（因为 709% >= 500%）；
- 5x `hard_rejection=false`；
- 最大总权重 `<= 1.0`；
- 报告没有订单、纸面目标或生产配置。

- [ ] **Step 3: 验证 sidecar 与 Markdown**

```bash
cd data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence
shasum -a 256 r388_turnover_attribution.json r388_turnover_attribution_result.md
```

- [ ] **Step 4: 提交最终证据**

```bash
git add data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution.json \
  data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution.json.sha256 \
  data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_result.md \
  data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r388_turnover_attribution_result.md.sha256
git commit -m "research: publish R388 frozen turnover attribution"
```

## Task 4: 全量验证、推送与 CI

**Files:**
- Review: all R388 files and final diff.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: clean main, pushed commit, CI status.

- [ ] **Step 1: 运行定向与回归测试**

```bash
cd backend
uv run pytest tests/test_r388_frozen_turnover_attribution.py tests/test_r387_forward_shadow_confirmation.py -q
uv run pytest tests/test_r381_pit_data_audit.py tests/test_r385_pit_platform_monte_carlo.py tests/test_r386_mc_advantage_confirmation.py -q
uv run ruff check ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r388_frozen_turnover_attribution.py tests/test_r388_frozen_turnover_attribution.py
cd ..
git diff --check
```

- [ ] **Step 2: 运行完整后端测试并区分既有失败**

```bash
cd backend
uv run pytest tests -q
```

Expected: R388/R387/R385/R386/R381 全部通过；若 P7/P8/P9 的既有本地状态失败，单独复核并记录，不修改 R388 逻辑。

- [ ] **Step 3: 推送**

```bash
git push origin main
```

- [ ] **Step 4: 观察 CI**

```bash
gh run list --branch main --limit 4 --json databaseId,workflowName,status,conclusion,headSha,url
```

- [ ] **Step 5: 汇报**

必须汇报：R388 换手归因、sleeve 占比、2x/3x/5x 结果、warning、是否改变策略参数、提交 SHA、CI 状态和剩余风险。
