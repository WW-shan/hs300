# R551A R550 策略级 GBM 压力测试

> `diagnostic_only`：这是合成行情诊断，不是真实收益结论，也不能启用策略。

## 标准轴

- 路径：100 条
- 情景：4 个
- 成本：[2, 3, 5]
- 固定股票数：300
- 评价交易日：504

## 情景结果

| 情景 | 超额 p05 | 超额 p50 | 超额 p95 | 正超额比例 | MDD p50 | Sharpe p50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| bear | -18.92% | -6.12% | 7.11% | 23.67% | -28.73% | -0.93 |
| bull | -15.98% | -4.21% | 11.77% | 28.17% | -9.88% | 0.70 |
| high_volatility | -25.81% | -7.42% | 14.47% | 24.83% | -27.56% | -0.21 |
| neutral | -17.90% | -5.89% | 7.85% | 23.33% | -15.91% | -0.35 |

## 成本结果

| 成本 | 年化 p50 | MDD p50 | 超额 p50 |
| ---: | ---: | ---: | ---: |
| 2x | -2.53% | -18.69% | -4.21% |
| 3x | -3.20% | -19.44% | -5.46% |
| 5x | -4.56% | -20.77% | -7.76% |

## 标准回执

- status：`complete`
- 约束错误：[]
- 图表：r551_r550_gbm_distribution.png, r551_r550_gbm_constraints.png

## 边界

- GBM has no real financial statements, dividend, fund-holding, sentiment or macro fields.
- The R550 score is frozen from the real-data portfolio; this tests its ranking and execution under synthetic prices, not its real-data alpha.
- The GBM adapter uses project matcher equal position sizing; the R550 vol_12 position policy is not applied in this synthetic diagnostic.
- 5x cost is warning-only and synthetic results cannot admit a strategy or generate orders.
