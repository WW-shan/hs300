# R444 四因子非价格风险预算

| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 27.81% | 19.24% | 10.96% | 0.881 | -11.69% | 6.79% | -10.22% | -13.62% |
| cash_20 | 21.87% | 15.49% | - | - | - | - | - | - |
| cash_30 | 18.97% | 13.59% | - | - | - | - | - | - |
| index_20_cash_10 | 18.02% | 14.24% | - | - | - | - | - | - |
| vol_target_10 | 23.17% | 7.09% | - | - | - | - | - | - |
| vol_target_15 | 28.73% | 11.78% | - | - | - | - | - | - |

- 选中：`baseline`
- 判定：**拒绝**

## 门槛

- selected_not_baseline: 未过
- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过

## 边界

- Index return is not an executable ETF NAV.
- Vol target uses only past 20-day realized strategy returns.
- Test/recent tail is confirmation only.
- No leverage and no hard 5x rejection.
- Current HS300 membership applied to history retains survivorship bias.
