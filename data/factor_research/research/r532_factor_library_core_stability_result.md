# R532 因子库核心时间稳定性与综合能力

## 冻结代表因子

| 因子 | 参数 | 聚类大小 | R531 训练 IC | R530 plateau |
| --- | ---: | ---: | ---: | --- |
| amount_level | 5 | 1 | 0.023543 | robust_plateau |
| atr_risk | 5 | 5 | 0.035085 | robust_plateau |
| dividend_yield_absolute | 0.015 | 2 | 0.0339 | robust_plateau |
| max_return | 60 | 1 | 0.042375 | robust_plateau |
| turnover_level | 5 | 1 | 0.022265 | robust_plateau |
| vol_of_vol | 20 | 1 | 0.031098 | robust_plateau |

## 核心表现

| 区间 | 中性 IC | NW t | 日期数 |
| --- | ---: | ---: | ---: |
| full | 0.041368 | 5.114256 | 1088 |
| validation | 0.029096 | 2.032049 | 439 |
| recent | 0.052005 | 2.489378 | 253 |

## 时间稳定性

| 年份 | 中性 IC | 日期数 |
| --- | ---: | ---: |
| 2022 | 0.024824 | 165 |
| 2023 | 0.070581 | 242 |
| 2024 | 0.0457 | 242 |
| 2025 | 0.006882 | 243 |
| 2026 | 0.056636 | 196 |

- 完整滚动窗口：40
- 滚动窗口中位 IC：0.040819
- 正 IC 窗口比例：1.0
- 最差滚动窗口 IC：0.003227
- bootstrap CI 为正的窗口比例：0.6
- 最差窗口 bootstrap CI 下限：-0.026194

## 留一因子诊断

| 移除因子 | full 配对差 | validation 配对差 | recent 配对差 |
| --- | ---: | ---: | ---: |
| amount_level | -0.000605 | -0.00177 | -0.000138 |
| atr_risk | -3.3e-05 | -0.000339 | -0.001515 |
| dividend_yield_absolute | 0.002247 | 0.00256 | 0.005005 |
| max_return | 0.002596 | 0.001294 | 0.001984 |
| turnover_level | -0.000328 | -0.000495 | 0.000102 |
| vol_of_vol | 0.000725 | -0.001007 | -0.002181 |

## 结论

- 总体结论：rejected
- 门槛：{'frozen_core_chain_valid': True, 'coverage_ge_0_90': True, 'full_ic_ge_0_01': True, 'validation_ic_positive': True, 'recent_ic_positive': True, 'yearly_positive_min_4_of_5': True, 'period_bootstrap_ci_positive': False, 'rolling_window_count_ge_24': True, 'rolling_median_positive': True, 'rolling_positive_ratio_ge_0_70': True, 'rolling_worst_ic_ge_minus_0_01': True, 'rolling_bootstrap_positive_ratio_ge_0_70': False, 'rolling_worst_bootstrap_ci_low_ge_minus_0_01': False}

## 边界

- The six representatives and all factor parameters are frozen from R530/R531.
- Rolling 252-day windows overlap by 231 days; their positive ratio is not an independent-sample rate.
- Block bootstrap is applied to daily IC and does not create a portfolio return stream.
- Leave-one-out results are diagnostic only and are not used to delete factors in this round.
- R526 membership and industry vintages are reconstructed research inputs.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- stability_gate_failed:period_bootstrap_ci_positive
- stability_gate_failed:rolling_bootstrap_positive_ratio_ge_0_70
- stability_gate_failed:rolling_worst_bootstrap_ci_low_ge_minus_0_01
