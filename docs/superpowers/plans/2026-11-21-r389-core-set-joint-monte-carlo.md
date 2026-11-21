# R389 核心因子集联合蒙特卡洛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不新增因子的前提下，验证当前 R386 冻结候选的 A/B/C 组合在成员权重、成员增减、持有期/评分阈值和组合参数同时扰动下是否仍具备训练/检验两段的稳健性。

**Architecture:** 复用 R385/R386 的真实 T+1 回测缓存和组合器。新增一个 append-only 的 R389 runner：先冻结 R386 参数与成员，再用预登记的 Latin Hypercube / 离散组合生成联合样本；每个样本只做组合层重算，不重新发明因子或策略。训练段用于选择，检验段只做最终确认。

**Tech Stack:** Python 3.11、Polars、pytest、R385/R386 runner、现有 R387 缓存与 factor-set evidence。

**Spec:** `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r386_mc_advantage_confirmation.json`

## Global Constraints

- 参数与成员在看结果前冻结；不得根据 holdout 重新选样。
- 基准候选必须是 R386 的 5 个冻结参数：
  `c_satellite_cap=0.13, equity_target_vol=0.17, overlay_lookback_days=110, overlay_target_vol=0.17, overlay_equity_weight_max=0.37`。
- R389 只允许扰动：A/B/C sleeve 权重、C 卫星上限、持仓评分阈值 `entry_score/exit_score`、持有期 `hold_days`、最大持仓数。
- 不使用杠杆；所有样本最大总权重 `<=1.0`。
- 2x 为主口径，3x 为压力口径；5x 仅 warning。
- 训练段固定为 `2022-05-05..2025-05-05`，检验段固定为 `2025-05-06..2026-11-20`；不得交换或按结果改切分。
- 不生成订单、纸面目标或生产配置；成功状态只能是 `joint_mc_complete_no_promotion`。

## Task 1: 预登记与红测

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc_preregistration.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc_preregistration.json.sha256`
- Create: `backend/tests/test_r389_core_set_joint_mc.py`

**Interfaces:**
- Consumes: R386 report, R387 runner/report, R387 cache.
- Produces: frozen parameter map, train/test windows, sample plan, output policy.

- [ ] **Step 1: 写预登记 JSON**

必须包含：

```json
{
  "round": "R389",
  "classification": "joint factor-set Monte Carlo; no factor invention, no production",
  "freeze": {
    "source_round": "R386",
    "parameters": {
      "c_satellite_cap": 0.13,
      "equity_target_vol": 0.17,
      "overlay_lookback_days": 110,
      "overlay_target_vol": 0.17,
      "overlay_equity_weight_max": 0.37
    }
  },
  "windows": {
    "train_start": "2022-05-05",
    "train_end": "2025-05-05",
    "test_start": "2025-05-06",
    "test_end": "2026-11-20"
  },
  "samples": {
    "latin_hypercube": 96,
    "seed": 389,
    "weight_perturbation": 0.15,
    "member_dropout_probability": 0.10
  },
  "cost_protocol": {
    "main": "2x",
    "stress": "3x",
    "warning_only": "5x",
    "five_x_can_hard_reject": false
  },
  "output_policy": {
    "status": "joint_mc_complete_no_promotion",
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

- [ ] **Step 2: 生成 sidecar**

```bash
shasum -a 256 r389_core_set_joint_mc_preregistration.json > r389_core_set_joint_mc_preregistration.json.sha256
```

- [ ] **Step 3: 写红测**

至少断言：

```python
def test_r389_freezes_parameters_and_train_test_split():
    ...

def test_r389_dropout_is_deterministic():
    ...

def test_r389_rejects_leverage():
    ...

def test_r389_output_is_append_only():
    ...
```

- [ ] **Step 4: 运行红测**

```bash
cd backend
uv run pytest tests/test_r389_core_set_joint_mc.py -q
```

Expected: FAIL，因为 runner 与输出尚未存在。

## Task 2: 实现联合采样和组合适配

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r389_core_set_joint_mc.py`
- Modify: `backend/tests/test_r389_core_set_joint_mc.py`

**Interfaces:**
- Consumes: `r385.combine_c`, `r385.compose_portfolio`, `r387` outputs.
- Produces: `generate_joint_samples`, `compose_joint_candidate`, `build_report`.

- [ ] **Step 1: 固定样本空间**

对每个样本：

- 权重：A/B/C 基准权重 `[0.5185325768, 0.3514674232, 0.13]`，乘以 `[1-0.15, 1+0.15]` 的扰动后归一化；
- 成员 dropout：以 0.10 概率移除 A/B/C 中任一腿，但至少保留一腿；
- 参数：在 R386 预登记范围内对 5 个组合参数做 `latin_hypercube(96, 5, 389)`；
- 交易参数：`entry_score ∈ {60,70,80}`、`exit_score ∈ {30,40,50}`、`hold_days ∈ {20,30,40}`、`max_positions ∈ {10,20,30}`；
- 所有样本不得修改 A/B/C 内部因子公式和信号。

- [ ] **Step 2: 生成样本 id**

样本 id 必须是确定的：

```python
sample_id = sha256_json({
    "round": "R389",
    "sample": index,
    "weights": normalized_weights,
    "dropout": sorted(dropout),
    "parameters": parameters,
    "execution": execution,
})
```

- [ ] **Step 3: 组合适配**

只复用 R385 的已实现组合器，不复制一套新的回测逻辑：

```python
bundle = runtime.compose_portfolio(
    ctx,
    assets,
    outputs[f"A_{cost}"][0],
    outputs[f"B_{cost}_fade05_h30_sl20"][0],
    c_curve,
    cost,
    parameters,
)
```

如果 dropout 包含某腿，按预登记的防御方式把该腿权重置 0，并把剩余腿重新归一化；不得用未来窗口重新选腿。

- [ ] **Step 4: 训练/检验评估**

每个样本必须报告：

```text
train_annual
train_sharpe
train_mdd
test_total_return
test_sharpe
test_mdd
max_weight_sum
no_leverage
sample_id
```

训练段用于门槛筛选；检验段只用于确认，不参与重新加权或样本选择。

- [ ] **Step 5: 运行定向测试**

```bash
cd backend
uv run pytest tests/test_r389_core_set_joint_mc.py -q
```

Expected: PASS，且 `max_weight_sum <= 1.0` 对所有样本成立。

## Task 3: 发布测试结果

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc.json.sha256`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc_result.md`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc_result.md.sha256`

**Interfaces:**
- Consumes: Task 2 runner.
- Produces: sample count, train/test distribution, pass rates, candidate decision, sidecars.

- [ ] **Step 1: 运行全部 96 个样本**

```bash
cd backend
uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r389_core_set_joint_mc.py
```

- [ ] **Step 2: 审计**

必须确认：

- 样本数 `96`，去重后 `>=90`；
- 训练段与检验段日期没有交叉；
- 2x 通过率、3x 通过率、5x warning 分开报告；
- 所有样本 `max_weight_sum <= 1.0`；
- 没有样本在测试段使用训练段的选参结果；
- `active_version=null`、`orders_generated=false`、`paper_target_generated=false`。

- [ ] **Step 3: 写 Markdown 和 sidecar**

- [ ] **Step 4: 提交**

```bash
git add data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r389_core_set_joint_mc* \
  backend/tests/test_r389_core_set_joint_mc.py \
  data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r389_core_set_joint_mc.py \
  docs/superpowers/plans/2026-11-21-r389-core-set-joint-monte-carlo.md
git commit -m "research: add R389 joint factor-set Monte Carlo"
```

## Task 4: 全量验证与推送

- [ ] 跑 R389/R388/R387/R386/R385/R381 定向测试。
- [ ] 跑 Ruff、`git diff --check`。
- [ ] 跑完整后端测试；区分 P7/P8/P9 既有状态失败。
- [ ] `git push origin main`。
- [ ] `gh run list` 并等待 CI + Docker 成功。

## Self-Review

- 覆盖：联合权重、成员 dropout、组合参数、执行参数、训练/检验隔离、成本、无杠杆和 append-only 均有测试。
- 未覆盖：真实盘中冲击、完整公告 vintage、A/B/C 内部因子公式变化；这些保留为明确限制。
- 没有把 R387 的 11 天表现当作 R389 选参依据。
