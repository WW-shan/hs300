# R446 防御 ETF 轮动

| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| momentum_20 | 15.77% | 7.52% | 8.68% | 0.504 | -26.68% | -12.87% | -27.65% | -40.53% |
| momentum_60 | 5.32% | 4.51% | -0.33% | 0.084 | -29.31% | -17.63% | -34.25% | -43.55% |
| momentum_120 | 6.24% | 8.67% | 3.34% | 0.261 | -26.11% | -9.64% | -32.41% | -40.85% |
| risk_adjusted_20 | 10.54% | 6.55% | 7.43% | 0.491 | -22.88% | -10.49% | -25.63% | -37.18% |
| risk_adjusted_60 | 6.22% | 1.27% | -4.40% | -0.134 | -32.28% | -19.99% | -36.69% | -43.72% |

- 选中：`momentum_20`
- 判定：**拒绝**

## 门槛

- has_eligible_rule: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 未过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过

## 边界

- ETF prices are forward-adjusted local enriched data; execution uses next open.
- Only six long-history ETFs are available; no overseas or broad commodity ETF.
- The test window has been observed before.
- No leverage; costs are 10/20 bps per rebalance.
- ETF tracking difference and liquidity impact are not fully modeled.
