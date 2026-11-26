# R550 cash_safety 固定变体确认

> 这是 R549 发现后的事后固定确认，不是独立样本；结论最多 `watch_only`。

## 结果

| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 10.38% | 0.94 | -12.82% | 53.54% | 195 |
| full 3x | 9.86% | 0.90 | -13.43% | 50.44% | 195 |
| full 5x | 8.83% | 0.82 | -14.67% | 44.36% | 195 |
| recent 2x | 3.62% | 0.38 | -11.23% | 3.73% | 52 |
| recent 3x | 3.05% | 0.33 | -11.37% | 3.13% | 52 |
| recent 5x | 1.81% | 0.22 | -11.73% | 1.86% | 52 |
| validation 2x | 0.51% | 0.10 | -6.44% | 0.38% | 40 |

## 与 R546 对比

- Full 年化差：-0.37%
- Recent 收益差：+3.58%

## Fresh starts

| 起始日 | 总收益 | 年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: | ---: |
| 2022-05-05 | 53.54% | 10.38% | 0.94 | -12.82% |
| 2023-01-03 | 35.46% | 8.58% | 0.80 | -10.78% |
| 2024-01-02 | 26.57% | 9.03% | 0.77 | -14.37% |
| 2025-01-02 | 6.33% | 3.53% | 0.37 | -9.28% |
| 2025-10-14 | 3.73% | 3.62% | 0.38 | -11.23% |
| 2026-01-05 | 8.02% | 10.10% | 0.88 | -10.78% |

## 门槛

- 核心通过：True
- 3× 压力通过：True
- Fresh-start：{'positive_count': 6, 'median': 0.17295, 'pass': True}
- 5× warnings：[]
- 失败项：[]
- 结论：`watch_only`

## 边界

- The cash_safety variant was nominated after R549 discovery, so this is a post-hoc confirmation and not independent OOS.
- R546 and earlier research already observed recent-year returns.
- The two add-on composites are fixed draft definitions with no weight search.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
