# R549B 质量复合正式加一组合

> recent 已在 R547/R548 观察过，本轮最多 `watch_only`。

## 选择

- 选中变体：`control`
- 加一因子：无

## 发现窗口

| 变体 | 训练年化 | 验证年化 | 近期总收益 | 选择 |
| --- | ---: | ---: | ---: | --- |
| control | 24.18% | 4.18% | -3.18% | selected |
| cash_quality | 23.54% | -0.60% | 1.64% |  |
| safety | 16.36% | -3.75% | -0.11% |  |
| cash_safety | 16.53% | 0.51% | 3.73% |  |

## 核心结果

| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 13.26% | 1.17 | -14.53% | 71.67% | 196 |
| full 3x | 12.72% | 1.13 | -15.13% | 68.16% | 196 |
| full 5x | 11.60% | 1.04 | -16.50% | 61.03% | 196 |
| recent 2x | -3.09% | -0.23 | -16.83% | -3.18% | 53 |
| recent 3x | -3.52% | -0.27 | -16.95% | -3.62% | 53 |
| recent 5x | -4.67% | -0.38 | -17.52% | -4.80% | 53 |
| validation 2x | 4.18% | 0.41 | -4.76% | 3.07% | 40 |

## 与 R546 对比

- Full 年化差：+2.51%
- Full MDD 差：-1.54%
- Recent 收益差：-3.33%

## Fresh starts

| 起始日 | 总收益 | 年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: | ---: |
| 2022-05-05 | 71.67% | 13.26% | 1.17 | -14.53% |
| 2023-01-03 | 45.16% | 10.64% | 0.98 | -11.97% |
| 2024-01-02 | 42.46% | 13.86% | 1.12 | -12.45% |
| 2025-01-02 | 8.29% | 4.61% | 0.47 | -9.15% |
| 2025-10-14 | -3.18% | -3.09% | -0.23 | -16.83% |
| 2026-01-05 | -4.36% | -5.41% | -0.43 | -14.59% |

## 门槛

- 核心通过：False
- 3× 压力通过：False
- Fresh-start：{'positive_count': 4, 'median': 0.25375, 'pass': True}
- 5× warnings：[]
- 失败项：['recent_return_positive', 'stress_3x_recent_min']
- 结论：`rejected`

## 边界

- R547/R548 already observed recent-year outcomes; R549 recent result is not independent OOS.
- Variant selection uses training and validation only; recent is reported as a risk diagnostic.
- The add-on composites are fixed draft definitions with no weight tuning.
- Five-times cost is warning-only and no leverage is used.
- No active version, orders, paper targets or production enablement are produced.
