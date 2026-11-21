# R447 防御 ETF 轮动

| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| momentum_20 | 13.16% | 1.93% | 11.97% | 0.651 | -22.68% | -8.67% | -23.02% | -36.81% |
| momentum_60 | 5.73% | 0.86% | 0.27% | 0.114 | -26.60% | -15.16% | -33.40% | -41.75% |
| momentum_120 | 6.34% | 4.10% | 2.50% | 0.223 | -25.59% | -8.28% | -32.47% | -39.50% |
| risk_adjusted_20 | 8.87% | 0.19% | 9.75% | 0.614 | -20.62% | -7.61% | -22.45% | -34.73% |
| risk_adjusted_60 | 6.57% | -1.97% | -2.81% | -0.050 | -28.76% | -16.74% | -35.01% | -41.56% |

- 选中：`momentum_120`
- 判定：**拒绝**

## 门槛

- has_eligible_rule: 未过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过

## 边界

- R446 used a misaligned open-to-open return loop and is superseded by R447.
- Execution is next trading day open after month-end signal.
- ETF prices are local forward-adjusted enriched data.
- No leverage; costs are 10/20 bps per rebalance.
- The test window has been observed before.
