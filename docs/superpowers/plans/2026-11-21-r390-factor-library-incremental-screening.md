# R390 因子库增量筛查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 P6 的 portfolio-ready 因子中，排除已研究、fixed、blocked 和同一参数族重复成员，构建一个可复现的剩余 alpha 候选集，再做相关性、边际 IC 和组合层 MC 的增量筛查。

**Architecture:** 新增只读候选构建器和 append-only 研究 runner。候选来自 P6 admission、P1-P4 参数族、P5/P8 已研究集合；先用 stock-alpha 角色和排除规则得到候选，再用与当前因子集的相关性聚类选代表。只有代表候选进入 P5 风格的边际 IC 和小规模组合验证。

**Tech Stack:** Python 3.11、Polars、pytest、现有 P5/P6/P8 研究管线。

**Spec:** `data/factor_research/automation/runs/1513dbc1e614f6ab31552bdb18771bdecf3162ad3b343e2681bda9b2e9cba298.json`

## Global Constraints

- 不新增因子公式；只研究现有 catalog 中 P6 portfolio-ready 的因子。
- 不重复 P1-P4 已研究参数族，不重复 P5/P8 已研究候选。
- fixed conditioner、blocked、exit_signal 不进入 stock-alpha 候选集。
- 2x 为硬主口径，3x 为硬压力口径；5x 只 warning。
- 无杠杆，最大总权重 <= 1.0。
- 训练/检验窗口固定为 `2022-05-05..2025-05-05` / `2025-05-06..2026-11-05`。
- 不生成订单、纸面目标或生产配置；成功状态只能是 `screening_complete_no_promotion`。

## Task 1: 构建可审计候选集

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r390_factor_library_screening_preregistration.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r390_factor_library_screening_preregistration.json.sha256`
- Create: `backend/tests/test_r390_factor_library_screening.py`

**Interfaces:**
- Consumes: P6 admission, `parameters/families.json`, P5 and P8 reports.
- Produces: deterministic `eligible_factor_ids`, exclusion reasons, and candidate count.

- [ ] **Step 1: 写预登记 JSON**

必须冻结：

```json
{
  "round": "R390",
  "classification": "incremental stock-alpha screening of remaining factor library",
  "windows": {
    "train_start": "2022-05-05",
    "train_end": "2025-05-05",
    "test_start": "2025-05-06",
    "test_end": "2026-11-05"
  },
  "exclusions": {
    "require_portfolio_ready": true,
    "exclude_p5_p8_studied": true,
    "exclude_fixed": true,
    "exclude_blocked": true,
    "exclude_conditioner": true,
    "exclude_exit_signal": true
  },
  "cost_protocol": {
    "main": "2x",
    "stress": "3x",
    "warning_only": "5x",
    "five_x_can_hard_reject": false
  },
  "output_policy": {
    "status": "screening_complete_no_promotion",
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

- [ ] **Step 2: 写红测**

至少覆盖：

```python
def test_candidate_builder_excludes_studied_fixed_blocked_and_conditioners():
    ...

def test_candidate_builder_is_deterministic():
    ...

def test_candidate_builder_requires_portfolio_ready():
    ...
```

- [ ] **Step 3: 运行红测**

```bash
cd backend
uv run pytest tests/test_r390_factor_library_screening.py -q
```

Expected: FAIL，因为构建器尚未存在。

## Task 2: 实现候选构建器

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r390_factor_library_screening.py`
- Modify: `backend/tests/test_r390_factor_library_screening.py`

**Interfaces:**
- Produces:
  - `eligible_factors() -> list[str]`
  - `exclusion_audit() -> dict[str, str]`
  - `family_representatives() -> list[str]`

- [ ] **Step 1: 读取权威输入**

必须读取：

```text
data/factor_research/automation/runs/1513dbc1e614f6ab31552bdb18771bdecf3162ad3b343e2681bda9b2e9cba298.json
data/factor_research/parameters/families.json
data/factor_research/research/p5_incremental_2026-11-12.json
data/factor_research/research/p8_financial_increment_2026-11-12.json
```

- [ ] **Step 2: 排除规则**

排除：

- `portfolio_ready != true`
- P5/P8 已研究 factor id
- `fixed_factor_ids`
- `blocked_factor_ids`
- P6 classification `blocked`
- 名称含 conditioner/regime/market/宏观条件的固定因子
- `exit_signal` 角色

- [ ] **Step 3: 参数族代表**

同一 family 只保留一个代表：

```text
优先选择 P1-P4 recommendation_status=recommended 的 factor；
如果该 family 已被 P5/P8 研究过，则整个 family 排除；
否则选 deterministic lexicographic factor id。
```

- [ ] **Step 4: 输出审计**

报告必须列出：

```text
raw_ready_count
excluded_studied_count
excluded_fixed_count
excluded_blocked_count
excluded_conditioner_count
excluded_duplicate_family_count
eligible_count
eligible_factor_ids
```

- [ ] **Step 5: 跑定向测试**

```bash
cd backend
uv run pytest tests/test_r390_factor_library_screening.py -q
```

Expected: PASS；候选数为确定性正整数。

## Task 3: 相关性与边际 IC 初筛

**Files:**
- Modify: `run_r390_factor_library_screening.py`
- Create: `data/.../r390_factor_library_screening.json`
- Create: `data/.../r390_factor_library_screening_result.md`

- [ ] **Step 1: 计算候选与当前 core 的最大平均绝对相关性**

当前 core 使用：

```text
core_v1 的 alpha 成员
```

如果候选与任一 core 成员的 `mean_abs` 相关性 `>= 0.90`，标记 `redundant`，不进入组合层。

- [ ] **Step 2: 计算边际 IC**

复用 P5 的 80/20 组合边际 IC：

```text
base_score = current core composite
candidate_score = candidate rank score
combined = 0.8 * base + 0.2 * candidate
```

门槛：

```text
marginal_ic > 0
NW p <= 0.10
BH q <= 0.10
```

- [ ] **Step 3: 输出候选层级**

每个候选必须有一个状态：

```text
statistical_rejected
redundant
portfolio_eligible
data_blocked
```

- [ ] **Step 4: 只把 portfolio_eligible 送入组合层**

组合层最多保留 12 个统计通过候选；如果超过，按 `min(train_ic,test_ic)` 降序取前 12，不让计算量失控。

## Task 4: 组合层 MC

**Files:**
- Modify: `run_r390_factor_library_screening.py`
- Test: `backend/tests/test_r390_factor_library_screening.py`

- [ ] **Step 1: 冻结组合样本**

对每个 `portfolio_eligible` 候选，生成：

```text
candidate_weight ∈ {0.05, 0.10, 0.15, 0.20}
max_positions ∈ {10, 20, 30}
holding_days ∈ {20, 30, 40}
```

共最多 `12 * 4 * 3 * 3 = 432` 个组合点，去重后运行。

- [ ] **Step 2: 与当前 core 对比**

每个组合点必须报告：

```text
full_2x_annual
full_3x_annual
recent_2x_total_return
rolling_positive_rate_2x
max_weight_sum
candidate_weight
```

- [ ] **Step 3: 准入门槛**

组合候选只有在以下条件全过时才进入 `watch_candidate`：

```text
full_2x_annual >= baseline_full_2x_annual - 0.01
full_2x_sharpe >= baseline_full_2x_sharpe - 0.05
full_2x_mdd >= baseline_full_2x_mdd - 0.02
recent_2x_total_return >= baseline_recent_2x_total_return - 0.01
full_3x_annual > 0
rolling positive rate >= baseline rolling positive rate
5x warning only
max_weight_sum <= 1.0
```

- [ ] **Step 4: 输出决策**

只允许：

```text
no_eligible_factors
all_statistical_candidates_rejected
portfolio_candidates_found_watch_only
```

`active_version` 保持 null。

## Task 5: 验证与提交

- [ ] 运行 R390 定向测试。
- [ ] 运行 R389/R388/R387/R386/R385/R381 回归。
- [ ] 运行 Ruff、`git diff --check`。
- [ ] 提交 R390 预登记、runner、证据和测试。
- [ ] 推送并观察 CI + Docker。

## Self-Review

- 覆盖：全库 ready 因子、排除规则、家族代表、相关性、边际 IC、组合 MC、5x warning 和无杠杆。
- 不覆盖：新增公式、财报字段扩张、盘中冲击和完整公告 vintage。
- 禁止：直接拿 R389 的稳定结果把未研究因子硬塞入 active 版本。
