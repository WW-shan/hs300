# R546 vol_12 风险预算确认

> R545 已观察 recent；本轮是非独立确认，最多 `watch_only`。

## 结果

| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 平均敞口 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| full 2× | 10.75% | 1.01 | -12.99% | 55.75% | 76.29% | 198 |
| full 3× | 10.20% | 0.96 | -13.18% | 52.42% | 76.28% | 198 |
| full 5× | 9.14% | 0.87 | -13.51% | 46.20% | 76.18% | 198 |
| recent 2× | 0.14% | 0.07 | -15.30% | 0.15% | 90.49% | 54 |
| recent 3× | -0.52% | 0.02 | -15.46% | -0.53% | 90.40% | 54 |
| recent 5× | -1.80% | -0.09 | -15.81% | -1.85% | 90.57% | 54 |
| validation 2× | 7.05% | 0.58 | -5.55% | 5.15% | 93.24% | 40 |

## 与对照

- Full 年化较 R543：-2.64%
- Recent 较 R543：-1.39%
- Full MDD 较 R543：+3.41%
- Full 年化较 R545 vol_10：+1.15%

## Fresh starts

| 起始日 | 总收益 | 年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: | ---: |
| 2022-05-05 | 55.75% | 10.75% | 1.01 | -12.99% |
| 2023-01-03 | 52.75% | 12.18% | 1.05 | -13.40% |
| 2024-01-02 | 47.86% | 15.43% | 1.19 | -15.76% |
| 2025-01-02 | 8.15% | 4.53% | 0.42 | -16.01% |
| 2025-10-14 | 0.15% | 0.14% | 0.07 | -15.30% |
| 2026-01-05 | -5.64% | -6.98% | -0.55 | -16.15% |

## 门槛

- Core：True
- 3× stress：True
- Fresh-start：{'positive_count': 5, 'median': 0.28005, 'pass': True}
- 5× warnings：['negative_5x_recent']
- 失败项：[]
- 结论：`watch_only`

## Bootstrap

- Full 2×：{'total_p05': 0.17940108909727792, 'total_p50': 0.5629648161947489, 'sharpe_p50': 1.0220449996444907, 'mdd_p05': -0.16657820325399075}
- Recent 2×：{'total_p05': -0.16539328753396187, 'total_p50': -0.0001462311595130661, 'sharpe_p50': 0.056206929654635376, 'mdd_p05': -0.21894060453070507}

## 边界

- R545 and prior recent-year results were already observed; this is not independent OOS.
- Only the pre-declared vol_12 budget is confirmed; no parameter search is performed.
- The policy uses causal selected-candidate recent returns and portfolio drawdown only.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
