# R454 财报质量 + 防御资产混合

| 股票仓数/持有 | 混合 | 训练年化 | 验证年化 |
| --- | --- | ---: | ---: |
| 10_30 | equity100 | 43.56% | 3.37% |
| 10_30 | equity80_defensive20_equal | 34.19% | 5.33% |
| 10_30 | equity70_defensive30_equal | 29.65% | 6.24% |
| 10_30 | equity80_defensive20_bond_gold | 36.61% | 7.38% |
| 10_30 | equity70_defensive30_bond_gold | 33.19% | 9.35% |
| 10_30 | equity75_defensive20_cash5 | 31.85% | 5.23% |
| 10_40 | equity100 | 18.92% | 6.04% |
| 10_40 | equity80_defensive20_equal | 15.42% | 7.56% |
| 10_40 | equity70_defensive30_equal | 13.64% | 8.24% |
| 10_40 | equity80_defensive20_bond_gold | 17.51% | 9.69% |
| 10_40 | equity70_defensive30_bond_gold | 16.74% | 11.45% |
| 10_40 | equity75_defensive20_cash5 | 14.48% | 7.34% |
| 20_30 | equity100 | 33.86% | 21.94% |
| 20_30 | equity80_defensive20_equal | 26.81% | 20.23% |
| 20_30 | equity70_defensive30_equal | 23.36% | 19.28% |
| 20_30 | equity80_defensive20_bond_gold | 29.10% | 22.62% |
| 20_30 | equity70_defensive30_bond_gold | 26.73% | 22.86% |
| 20_30 | equity75_defensive20_cash5 | 25.02% | 19.14% |
| 20_40 | equity100 | 36.26% | 19.60% |
| 20_40 | equity80_defensive20_equal | 28.65% | 18.39% |
| 20_40 | equity70_defensive30_equal | 24.93% | 17.70% |
| 20_40 | equity80_defensive20_bond_gold | 30.97% | 20.71% |
| 20_40 | equity70_defensive30_bond_gold | 28.34% | 21.17% |
| 20_40 | equity75_defensive20_cash5 | 26.73% | 17.43% |

- 选中：`20_30 / equity70_defensive30_bond_gold`
- 判定：**拒绝**
- 检验年化：10.35%
- 检验 Sharpe：0.883
- 检验 MDD：-21.60%
- 近一年年化：4.67%
- 检验 p05：-12.92%
- 近一年 p05：-19.85%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 未过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- equity_plus_defensive_improves_tail: 通过
- no_leverage: 通过

## 结论

把财报复合因子与债券/黄金防御资产混合，能够改善一部分收益和近一年表现，但检验段最大回撤达到 -21.60%，检验与近一年 bootstrap 下界仍为负。这个混合结构可以作为下一轮研究对象，但不能作为本轮推荐策略。

## 边界

- Equity sleeve uses current HS300 membership and retains survivorship bias.
- Defensive ETF history is local ignored data; the script fails closed when it is absent.
- ETF and equity sleeves use different execution models; this is a research mix, not a broker-level portfolio recommendation.
- The test and recent windows have been observed previously.
- 5x ETF cost is warning-only.
- No leverage, orders, paper targets, or production activation.
