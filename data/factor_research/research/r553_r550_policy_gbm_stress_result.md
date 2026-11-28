# R553 R550 vol_12 策略级 GBM 压力测试

> `diagnostic_only`：使用真实 R550 分数、合成 GBM 报价和项目 vol_12 仓位策略。

## 标准轴

- 路径：100 条
- 情景：4 个
- 成本：[2, 3, 5]
- 仓位策略：`vol_12`

## 情景结果

| 情景 | 超额 p05 | 超额 p50 | 正超额比例 | MDD p50 | Sharpe p50 |
| --- | ---: | ---: | ---: | ---: | ---: |
| bear | -12.08% | 2.40% | 61.00% | -18.75% | -0.94 |
| bull | -17.64% | -6.37% | 19.50% | -8.24% | 0.66 |
| high_volatility | -25.55% | -1.90% | 42.50% | -16.89% | -0.22 |
| neutral | -15.90% | -3.82% | 29.50% | -12.25% | -0.35 |

## 成本结果

| 成本 | 年化 p50 | MDD p50 | 超额 p50 |
| ---: | ---: | ---: | ---: |
| 2x | -1.42% | -13.41% | -1.83% |
| 3x | -1.87% | -13.65% | -2.72% |
| 5x | -2.84% | -14.63% | -4.46% |

## 回执

- 约束错误：[]
- 图表：r553_r550_policy_gbm_distribution.png, r553_r550_policy_gbm_constraints.png

## 边界

- This is a synthetic GBM stress test, not real HS300 alpha evidence.
- The score matrix is frozen from R550; synthetic prices intentionally contain no financial-factor signal.
- The vol_12 policy is applied through the project StrategyBacktestService and causal PositionSizingContext.
- 5x cost is warning-only and synthetic results cannot admit, order or enable a strategy.
