# R462 value_quality 现金缓冲验证

| 暴露 | 仓数 | 持有 | 训练年化 | 验证年化 |
| ---: | ---: | ---: | ---: | ---: |
| 40% | 10 | 30 | 7.90% | 7.24% |
| 40% | 10 | 40 | 7.51% | 6.48% |
| 40% | 20 | 30 | 6.62% | 5.51% |
| 40% | 20 | 40 | 6.87% | 6.49% |
| 60% | 10 | 30 | 11.91% | 10.67% |
| 60% | 10 | 40 | 11.38% | 9.78% |
| 60% | 20 | 30 | 10.03% | 7.84% |
| 60% | 20 | 40 | 10.42% | 9.70% |
| 80% | 10 | 30 | 15.94% | 13.83% |
| 80% | 10 | 40 | 15.20% | 13.14% |
| 80% | 20 | 30 | 13.49% | 10.20% |
| 80% | 20 | 40 | 14.01% | 12.94% |
| 100% | 10 | 30 | 20.20% | 16.68% |
| 100% | 10 | 40 | 19.06% | 16.44% |
| 100% | 20 | 30 | 17.01% | 12.39% |
| 100% | 20 | 40 | 17.79% | 16.17% |

- 选中：100% 暴露 / 10 仓 / 30 日
- 判定：**拒绝**
- 检验年化：17.32%
- 检验 Sharpe：1.338
- 检验 MDD：-11.73%
- 近一年年化：-1.88%
- 检验 p05：-1.54%
- 近一年 p05：-22.55%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 未过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- max_exposure_at_most: 通过
- no_leverage: 通过

## 结论

降低股票暴露可以等比例降低回撤和尾部，但不能改变近一年的负收益方向；训练/验证选中的仍是 100% 股票暴露。该结果说明现金缓冲只能削弱风险，不能创造近期 alpha，因此拒绝。

## 边界

- Cash exposure is a conservative portfolio budget, not leverage.
- No cash yield is modeled.
- The test and recent windows have been observed before.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost remains warning-only.
