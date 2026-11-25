# R544 稳定 7 因子参数平台与 Monte Carlo

> recent 已观察，本轮最多 `watch_only`；参数选择只使用训练期。

## 选中参数

- 参数：`{'max_positions': 10, 'max_hold_days': 80, 'industry_cap': 3, 'stop_variant': 'stop_12', 'stop_loss': -0.12}`
- 邻域统计：`{'neighbors': 35, 'pass_rate': 1.0, 'median_annual': 0.2423, 'median_sharpe': 1.54, 'median_mdd': -0.1115}`
- 训练 bootstrap：`{'total_p05': 0.38925559928409115, 'total_p50': 0.9081442272019569, 'sharpe_p50': 1.7045731232122132, 'mdd_p05': -0.16738350118579348}`

## 确认结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 15.18% | 1.03 | -20.94% | 84.71% | 157 |
| recent 2x | 3.64% | 0.34 | -13.28% | 3.74% | 44 |
| validation 2x | -3.14% | -0.18 | -8.60% | -2.33% | 32 |

## 与 R543 默认参数取舍

- Full 年化差：+1.79%
- Recent 收益差：+2.20%

## 成本压力

| 成本 | Full 年化 | Full Sharpe | Full MDD | Recent 总收益 |
| --- | ---: | ---: | ---: | ---: |
| 2x | 15.18% | 1.03 | -20.94% | 3.74% |
| 3x | 14.63% | 1.00 | -21.59% | 3.10% |
| 5x | 13.49% | 0.93 | -22.89% | 1.86% |

## 平台 Finalists

| # | 参数 | 邻居通过率 | 邻居 Sharpe 中位数 | Bootstrap p05 |
| ---: | --- | ---: | ---: | ---: |
| 1 | `{'max_positions': 10, 'max_hold_days': 80, 'industry_cap': 4, 'stop_variant': 'stop_12', 'stop_loss': -0.12}` | 100.00% | 1.54 | 31.92% |
| 2 | `{'max_positions': 10, 'max_hold_days': 80, 'industry_cap': 4, 'stop_variant': 'stop_10', 'stop_loss': -0.1}` | 100.00% | 1.54 | 33.44% |
| 3 | `{'max_positions': 10, 'max_hold_days': 80, 'industry_cap': 3, 'stop_variant': 'stop_12', 'stop_loss': -0.12}` | 100.00% | 1.54 | 38.93% |
| 4 | `{'max_positions': 10, 'max_hold_days': 80, 'industry_cap': 4, 'stop_variant': 'stop_08', 'stop_loss': -0.08}` | 100.00% | 1.54 | 29.32% |
| 5 | `{'max_positions': 15, 'max_hold_days': 80, 'industry_cap': 4, 'stop_variant': 'stop_12', 'stop_loss': -0.12}` | 100.00% | 1.54 | 31.72% |

## 门槛

- 核心通过：False
- 3× 压力通过：True
- Fresh-start：{'positive_count': 5, 'median': 0.17335, 'pass': True}
- 5× warnings：[]
- 失败项：['full_mdd_min', 'validation_annual_positive']
- 结论：`rejected`

## 边界

- R543 recent-year results were already observed; R544 recent is not independent OOS.
- Parameter selection uses training-only 2x data; validation and recent are confirmation only.
- Neighborhood statistics describe local sensitivity, not causal parameter effects.
- The selected parameter point can still be affected by the earlier R542/R543 observation process.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
