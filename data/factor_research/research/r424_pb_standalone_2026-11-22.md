# R424 纯低 PB 策略确认

| 仓数 | 持有 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 30 | 17.42% | 27.22% | -8.21% | -0.530 | -32.71% | -8.33% | -9.41% | -33.59% | -25.57% |
| 10 | 40 | 16.62% | 23.00% | -4.70% | -0.260 | -29.80% | -14.46% | -5.71% | -29.66% | -30.19% |
| 20 | 30 | 11.10% | 20.51% | -5.68% | -0.390 | -28.73% | -11.63% | -6.88% | -28.94% | -26.42% |
| 20 | 40 | 12.69% | 26.97% | -6.91% | -0.490 | -28.81% | -10.38% | -7.91% | -30.01% | -25.81% |

- 选中：10 仓 / 30 日
- 判定：**拒绝**

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- recent_annual_min: 未过
- stress_3x_positive: 未过

## 边界

- PB IC is neutralized, while the standalone portfolio is not industry/size-neutralized at execution.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- Single-factor strategies are exposed to valuation-regime concentration.
