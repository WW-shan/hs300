# R428 PB+OCF+低波组合确认

| 仓数 | 持有 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 30 | -7.94% | 17.52% | -4.94% | -0.240 | -27.98% | -15.30% | -6.19% | -32.13% | -34.50% |
| 10 | 40 | -9.30% | 26.69% | -9.08% | -0.570 | -27.16% | -11.12% | -10.11% | -33.20% | -29.96% |
| 20 | 30 | 1.28% | 18.23% | -2.36% | -0.120 | -21.63% | -7.94% | -3.53% | -24.74% | -25.86% |
| 20 | 40 | 2.64% | 28.02% | -1.85% | -0.080 | -21.95% | -7.45% | -2.78% | -23.59% | -25.00% |

- 选中：20 仓 / 40 日
- 判定：**拒绝**

## 门槛

- train_annual_min: 未过
- validation_annual_min: 通过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- recent_annual_min: 未过
- stress_3x_positive: 未过

## 边界

- The combination is motivated by observed PB IC and pure-PB failure; it is not an independent discovery.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
