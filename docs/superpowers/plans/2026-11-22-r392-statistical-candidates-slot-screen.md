# R392 统计通过候选组合层替换筛查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对 R391 的 11 个 statistical_pass 候选逐一加入当前 core_v1 watch 组合，做真实 T+1、2×/3× 成本和有限参数组合 MC，确认哪些候选能在组合层保留增量。

**Architecture:** 复用 P5 的 factor-set portfolio 入口和 R391 的候选分层。每个候选只改变一个槽位权重，组合层做 `candidate_weight ∈ {0.05,0.10,0.15,0.20}`、`max_positions ∈ {10,20,30}`、`holding_days ∈ {20,30,40}` 的去重网格。先训练/检验，再 2×/3× 硬门槛，5× 只 warning。

**Tech Stack:** Python 3.11、Polars、pytest、现有 `factor_rank_research` T+1 回测。

**Spec:** `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r391_factor_library_statistical.json`

## Global Constraints

- 只评估 R391 `statistical_pass` 的 11 个候选，不新增因子。
- base 固定为 `core_v1` 当前 watch alpha 4 腿。
- 所有组合无杠杆，最大总权重 <= 1.0。
- 2× 为主硬门槛，3× 为硬压力，5× 只 warning。
- 训练 `2022-05-05..2025-05-05`，检验 `2025-05-06..2026-11-05`。
- 不生成订单、纸面目标或生产配置；成功状态只能是 `portfolio_mc_complete_no_promotion`。

## Task 1: 预登记与红测

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r392_statistical_candidates_preregistration.json`
- Create: `...json.sha256`
- Create: `backend/tests/test_r392_statistical_candidates.py`

- [ ] **Step 1: 写预登记**

```json
{
  "round": "R392",
  "classification": "portfolio MC of R391 statistical candidates; no promotion",
  "source_round": "R391",
  "candidate_source": "statistical_pass",
  "windows": {
    "train_start": "2022-05-05",
    "train_end": "2025-05-05",
    "test_start": "2025-05-06",
    "test_end": "2026-11-05"
  },
  "grid": {
    "candidate_weight": [0.05, 0.10, 0.15, 0.20],
    "max_positions": [10, 20, 30],
    "holding_days": [20, 30, 40]
  },
  "cost_protocol": {
    "main": "2x",
    "stress": "3x",
    "warning_only": "5x",
    "five_x_can_hard_reject": false
  },
  "output_policy": {
    "status": "portfolio_mc_complete_no_promotion",
    "active_version": null,
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

- [ ] **Step 2: 红测**

```python
def test_candidates_are_exactly_r391_statistical_pass(): ...
def test_grid_is_deterministic_and_bounded(): ...
def test_no_leverage_gate(): ...
def test_output_is_append_only(): ...
```

- [ ] **Step 3: 运行红测**

```bash
cd backend
uv run pytest tests/test_r392_statistical_candidates.py -q
```

Expected: FAIL。

## Task 2: 组合层实现

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r392_statistical_candidates.py`
- Modify: `backend/tests/test_r392_statistical_candidates.py`

- [ ] **Step 1: 读取 R391 的 11 个候选**

必须校验 R391 sidecar、候选状态和 `active_version is None`。

- [ ] **Step 2: 构造 base 与 candidate scoring**

base：

```text
core_v1 watch alpha 成员及权重
```

candidate 组合：

```text
score = (1 - candidate_weight) * base_composite + candidate_weight * candidate_rank_score
```

candidate 权重必须非负且总和 1。

- [ ] **Step 3: 运行网格**

每个候选 `4 × 3 × 3 = 36` 个点，11 个候选共 396 个点；同参数去重后运行。

每个点报告训练/检验：

```text
annual_2x, sharpe_2x, mdd_2x, total_2x
annual_3x, mdd_3x
5x warning
max_weight_sum
```

- [ ] **Step 4: 门槛**

候选只有全部满足才进入 `portfolio_candidate_watch`：

```text
train_2x_annual >= base_train_2x_annual - 0.01
train_2x_sharpe >= base_train_2x_sharpe - 0.05
test_2x_total_return >= base_test_2x_total_return - 0.01
test_2x_sharpe >= base_test_2x_sharpe - 0.05
test_2x_mdd >= base_test_2x_mdd - 0.02
test_3x_total_return > 0
max_weight_sum <= 1.0
```

5× 只写 warning，不参与硬否决。

- [ ] **Step 5: 决策**

```text
no_portfolio_candidate
portfolio_candidates_found_watch_only
```

`active_version` 保持 null。

## Task 3: 发布、验证、提交

- [ ] 生成 JSON/Markdown/sidecar。
- [ ] 跑 R392/R391/R390/R389/R388/R387/R386/R385/R381 定向测试。
- [ ] Ruff、`git diff --check`。
- [ ] 提交并推送。
- [ ] 观察 CI 和 Docker。

## Self-Review

- 覆盖：11 个候选、权重网格、持仓/持有期、2×/3×、5× warning、无杠杆。
- 不覆盖：盘中冲击、完整 PIT vintage。
- 禁止：用 5× 或 11 天 R387 窗口硬否决。
