# R391 因子库统计增量筛查 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对 R390 的 114 个剩余 stock-alpha 候选统一计算与 core_v1 的相关性、80/20 边际 IC、NW p、BH q 和训练/检验稳定性，产出可审计的候选分层，不进入组合层。

**Architecture:** 复用 P5 的 `FactorBacktestService` panel 加载、Rank score、边际 IC 和 BH-FDR 逻辑；冻结 R390 候选列表和 core_v1 watch 版本。输出 append-only JSON/Markdown，只标记 `statistical_pass`、`redundant`、`statistical_rejected`、`data_unavailable`。

**Tech Stack:** Python 3.11、Polars、pytest、现有 P5/FactorResearch 管线。

**Spec:** `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r390_factor_library_screening.json`

## Global Constraints

- 候选列表必须完全来自 R390 `candidate_audit.eligible_factor_ids`，不得新增或删除。
- core 基准固定为 `core_v1` 的 watch alpha 成员及其权重。
- 2x 为正常成本口径；本阶段只做 IC/相关性，不生成交易组合。
- 不生成订单、纸面目标或生产配置。
- 不使用 R387 的 11 天结果调参。
- 通过候选只进入下一阶段组合 MC，不等于策略通过。

## Task 1: 预登记与红测

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r391_factor_library_statistical_preregistration.json`
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r391_factor_library_statistical_preregistration.json.sha256`
- Create: `backend/tests/test_r391_factor_library_statistical.py`

**Interfaces:**
- Consumes: R390 eligible IDs, R390 sidecar, core_v1 watch version.
- Produces: frozen candidate list, core members, gate thresholds.

- [ ] **Step 1: 写预登记 JSON**

```json
{
  "round": "R391",
  "classification": "statistical screening only; no portfolio promotion",
  "source_round": "R390",
  "windows": {
    "start": "2022-05-05",
    "end": "2026-11-05",
    "recent_start": "2025-10-14"
  },
  "gates": {
    "marginal_ic_positive": true,
    "nw_p_max": 0.10,
    "bh_q_max": 0.10,
    "max_mean_abs_correlation_max": 0.90
  },
  "output_policy": {
    "status": "statistical_screening_complete_no_promotion",
    "active_version": null,
    "orders_generated": false,
    "paper_target_generated": false,
    "production_enabled": false
  }
}
```

- [ ] **Step 2: 写红测**

测试必须断言：

```python
assert set(module.candidate_ids()) == set(r390_ids)
assert module.core_ids()
assert module.load_r390().candidate_count == 114
```

并要求输出 append-only、R390 sidecar 校验、5x 不参与本阶段决策。

## Task 2: 复用 P5 panel 和边际 IC

**Files:**
- Create: `data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r391_factor_library_statistical.py`
- Modify: `backend/tests/test_r391_factor_library_statistical.py`

- [ ] **Step 1: 加载 panel**

使用 P5 相同的 `FactorBacktestService`：

```text
factor_names = core_ids + candidate_ids
start = 2022-05-05
end = 2026-11-05
forward_return = 5d
minimum cross section = 30
```

- [ ] **Step 2: 计算 core composite**

使用 core_v1 watch alpha 权重和方向，缺失日 coverage < 0.5 时置空。

- [ ] **Step 3: 计算相关性**

对每个候选计算与 core 每个 alpha 的日均截面 Spearman 相关性，报告：

```text
max_mean_abs_correlation
mean_abs_correlation
```

- [ ] **Step 4: 计算边际 IC**

对每个候选：

```text
combined = 0.8 * core_composite + 0.2 * candidate_rank_score
marginal_delta = IC(combined) - IC(base)
```

使用 NW lag=5，计算 p 值和 BH q 值。

- [ ] **Step 5: 候选分类**

```text
redundant: max_mean_abs_correlation >= 0.90
statistical_pass: marginal_ic > 0 and p <= 0.10 and q <= 0.10 and not redundant
statistical_rejected: otherwise
data_unavailable: no panel column or < 120 valid dates
```

## Task 3: 发布并提交

- [ ] 生成 JSON/Markdown/sidecar。
- [ ] 定向测试通过。
- [ ] Ruff、`git diff --check`。
- [ ] 提交 R391。
- [ ] 推送并观察 CI。

## Self-Review

- 覆盖：R390 全候选、core 基准、相关性、边际 IC、NW、BH、稳定性。
- 不覆盖：组合收益、交易成本、真实资金容量。
- 禁止：把 statistical_pass 直接写成 watch 或 active。
