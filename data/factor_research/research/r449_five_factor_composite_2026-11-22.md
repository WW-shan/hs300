# R449 五因子组合与行业中性化

| 组合 | 仓数 | 行业上限 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 四因子 | 10 | - | 20.98% | 21.07% | 3.03% | 0.300 | -14.14% | 8.99% | 1.67% |
| 四因子 | 10 | 2 | 18.02% | 20.19% | 7.06% | 0.610 | -14.00% | 9.05% | 5.72% |
| 四因子 | 10 | 3 | 15.20% | 21.07% | 8.74% | 0.720 | -10.94% | 6.71% | 7.33% |
| 四因子 | 20 | - | 18.39% | 21.98% | 8.36% | 0.720 | -11.65% | 4.56% | 6.96% |
| 四因子 | 20 | 2 | 10.38% | 10.52% | 5.64% | 0.590 | -11.19% | 5.21% | 4.56% |
| 四因子 | 20 | 3 | 10.64% | 8.73% | 5.84% | 0.580 | -10.96% | 3.16% | 4.75% |
| 五因子 | 10 | - | 22.75% | 26.55% | 3.75% | 0.350 | -15.00% | 6.29% | 2.36% |
| 五因子 | 10 | 2 | 22.38% | 18.68% | 3.25% | 0.330 | -9.95% | 4.95% | 2.14% |
| 五因子 | 10 | 3 | 24.81% | 20.94% | 2.93% | 0.290 | -12.15% | 0.60% | 1.68% |
| 五因子 | 20 | - | 17.94% | 28.62% | 5.48% | 0.500 | -13.10% | 1.79% | 4.21% |
| 五因子 | 20 | 2 | 11.28% | 13.75% | 0.00% | 0.050 | -10.73% | 1.27% | -0.90% |
| 五因子 | 20 | 3 | 11.07% | 13.85% | 0.49% | 0.100 | -11.25% | 2.54% | -0.50% |

- 选中：五因子 10 仓，行业上限 无，持有 30 日
- 判定：**拒绝**
- 对四因子检验年化增量：0.72%

## 门槛

- has_eligible_rule: 通过
- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- five_factor_beats_four_factor: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- industry_size_neutral_signal: 未过
- no_leverage: 通过

## 行业+规模中性 IC

- `ext_alt_hs300_cash_flow_stability_4q_low`: 60日 IC 0.0401, p=0.00053043, 年度同号=0.8, 近一年 IC=0.0272
- `ext_alt_hs300_dividend_yield`: 60日 IC 0.0342, p=0.0824972, 年度同号=0.625, 近一年 IC=0.1075
- `ext_alt_hs300_fund_holding_ratio_low`: 60日 IC 0.0254, p=0.09857397, 年度同号=0.5, 近一年 IC=-0.0134
- `ext_alt_hs300_idio_vol_40d_low`: 60日 IC 0.0259, p=0.10261595, 年度同号=0.625, 近一年 IC=0.0849
- `ext_alt_hs300_vol_of_vol_60d_low`: 60日 IC 0.0280, p=0.00339464, 年度同号=0.875, 近一年 IC=0.0941

## 结论

五因子相对四因子只增加了 0.72% 的检验年化，但 Sharpe 未过门槛，检验和近一年 bootstrap p05 都为负；行业上限为 2/3 时超额明显收缩，说明组合收益依赖少数行业。该组合不进入可执行候选。

## 边界

- The five-factor membership is dual-admitted as of R439 but the added vol-of-vol leg has weaker marginal IC than the four-factor control.
- The test and recent windows have been observed in earlier rounds.
- Current HS300 membership applied to history retains survivorship bias.
- 5x cost is warning-only and is never a hard rejection.
- The composite is a research candidate until all gates pass.
