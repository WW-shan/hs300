# R543 稳定 IC 因子子集

> 这是非独立确认：R542 已观察 recent，结果最多为 `watch_only`。

## 因子集合

- 纳入：`amount_level`, `bollinger_width`, `dividend_yield_quantile`, `fund_holding_ratio_absolute`, `max_return`, `turnover_level`, `vol_of_vol`
- 排除：`fund_count_quantile`, `lhb_inst_net_buy_event`, `lhb_net_buy_holding`, `market_down_beta`, `repurchase_event_decay`, `repurchase_holding`
- 规则：train IC > 0 且 validation IC > 0；recent 不参与筛选。

## 核心结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 13.39% | 0.96 | -16.40% | 72.58% | 198 |
| recent 2x | 1.50% | 0.18 | -15.95% | 1.54% | 54 |
| validation 2x | 8.02% | 0.62 | -5.64% | 5.86% | 40 |

## 与 baseline 的取舍

- full 年化差：-3.33%
- full Sharpe 差：-0.18
- recent 收益差：+2.45%
- recent MDD 差：+2.86%
- recent 对沪深300：+10.92%
- recent 对 PIT 等权：+2.15%

## 成本压力

| 成本 | full 年化 | full Sharpe | full MDD | recent 总收益 |
| --- | ---: | ---: | ---: | ---: |
| 2x | 13.39% | 0.96 | -16.40% | 1.54% |
| 3x | 12.68% | 0.92 | -16.62% | 0.81% |
| 5x | 11.29% | 0.83 | -17.03% | -0.69% |

## Fresh starts

| 起始日 | 总收益 | 年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: | ---: |
| 2022-05-05 | 72.58% | 13.39% | 0.96 | -16.40% |
| 2023-01-03 | 60.13% | 13.62% | 0.99 | -15.78% |
| 2024-01-02 | 49.67% | 15.94% | 1.03 | -18.05% |
| 2025-01-02 | 9.24% | 5.13% | 0.44 | -17.94% |
| 2025-10-14 | 1.54% | 1.50% | 0.18 | -15.95% |
| 2026-01-05 | -5.92% | -7.33% | -0.49 | -18.31% |

## 门槛

- 核心通过：True
- 3× 压力通过：True
- Fresh-start：{'positive_count': 5, 'median': 0.29455, 'pass': True}
- 5× warnings：[]
- 失败项：[]
- 结论：`watch_only`

## Bootstrap

- full 2x：{'total_p05': 0.17879572895506457, 'total_p50': 0.7267481469039441, 'sharpe_p50': 0.9667623737474348, 'mdd_p05': -0.2241588599986829}
- recent 2x：{'total_p05': -0.16666595301253706, 'total_p50': 0.015536219754414882, 'sharpe_p50': 0.18180576430860262, 'mdd_p05': -0.2323977663079998}

## 边界

- R542 recent-year results were already observed; R543 recent is not independent OOS.
- This round changes only the factor member set; execution parameters remain frozen.
- The seven-factor rule uses train and validation IC only, but factor observation can still create selection bias.
- The 7-factor equal-weight mean-available composite can differ from a missing-data-penalized weighted composite.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
