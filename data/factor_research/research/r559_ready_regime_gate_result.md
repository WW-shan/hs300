# R559 可直接回测策略的 T-1 市场环境门控

> 六条门控在训练期选择，验证期确认；近期仅披露。无杠杆，2x 为基线，3x 为压力测试，5x 仅警告。

## 选中门控

- gate：`risk_on_combo`
- regime_filter：`{'states': ['strong', 'lean_strong', 'range'], 'min_score': 40}`
- 训练年化：29.23%
- 验证年化：4.14%

## 结果

| 口径 | 区间 | 收益 | 年化 | Sharpe | MDD | 交易 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 门控 | 2022-05-05 → 2026-11-05 | 107.01% | 18.25% | 1.32 | -13.07% | 194 |
| 无门控 | 2022-05-05 → 2026-11-05 | 74.59% | 13.70% | 1.13 | -11.58% | 195 |
| 门控验证 | 2025-01-01 → 2025-10-13 | 3.04% | 4.14% | 0.38 | -5.48% | 35 |
| 无门控验证 | 2025-01-01 → 2025-10-13 | 0.55% | 0.75% | 0.12 | -6.97% | 41 |
| 门控近期 | 2025-10-14 → 2026-11-05 | -0.33% | -0.33% | 0.03 | -15.13% | 53 |

## 稳健性

- 新鲜起点：4/5 为正，中位 3.51%。
- 长阶段起点：9/10 为正，中位 17.62%。
- 3x 全周期年化：17.61%。
- 5x 全周期年化：16.31%（仅警告）。

## 门槛

- full_annual_min: 通过
- full_sharpe_min: 通过
- full_mdd_min: 通过
- validation_annual_positive: 通过
- validation_beats_always_on: 通过
- stress_3x_annual_min: 通过
- recent_return_positive: 未过
- fresh_positive_min: 通过
- fresh_median_positive: 通过
- long_phase_positive_min: 通过
- long_phase_median_positive: 通过
- formal_evidence: 通过

## GBM

- 路径数：100
- 观测数：2400
- 约束错误：[]

## 边界

- Gate selection uses the 2022-05-05 to 2024-12-31 training window only; validation confirms the selected gate and never reselects among six gates.
- Recent windows have already been observed in earlier research and are disclosure-only, not independent out-of-sample evidence.
- The ready strategy's rank factor had negative standalone validation IC in R556B; R559 tests a market-regime overlay, not new factor alpha.
- GBM uses a deterministic neutral synthetic regime because the model contains no real market state; GBM validates risk mechanics, not real alpha.
- 2x cost is the no-leverage baseline, 3x is the stress gate, and 5x is warning-only rather than a hard rejection.
- No active version, orders, paper targets or production enablement are produced.
