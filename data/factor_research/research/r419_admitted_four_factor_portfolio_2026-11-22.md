# R419 四因子准入组合确认

| 仓数 | 持有 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 30 | 17.05% | 27.60% | 4.77% | 0.430 | -12.68% | 3.56% |
| 10 | 40 | 21.03% | 25.98% | 6.02% | 0.530 | -12.06% | 5.00% |
| 20 | 30 | 12.74% | 27.28% | 4.29% | 0.410 | -14.71% | 3.04% |
| 20 | 40 | 19.97% | 32.28% | 4.96% | 0.460 | -14.56% | 4.01% |

- 选中：10 仓 / 40 日
- 判定：**通过**

## 门槛

- has_platform: 通过
- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- stress_3x_positive: 通过
- recent_positive: 通过

## 边界

- R418 admission is based on 5-day IC and yearly consistency, not portfolio causality.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- The grid is small and pre-registered; no factor formulas change.
