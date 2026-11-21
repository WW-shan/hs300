# R442 双准入因子组合确认

| 仓数 | 持有 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 30 | 20.98% | 21.07% | 3.03% | 0.300 | -14.14% | 8.99% | 1.67% | -19.02% | -9.17% |
| 10 | 40 | 27.18% | 19.26% | 11.28% | 0.910 | -11.69% | 6.75% | 10.18% | -10.59% | -13.39% |
| 20 | 30 | 18.39% | 21.98% | 8.36% | 0.720 | -11.65% | 4.56% | 6.96% | -11.37% | -13.89% |
| 20 | 40 | 18.89% | 28.14% | 5.30% | 0.480 | -15.12% | 0.30% | 4.35% | -15.71% | -17.48% |

- 选中：10 仓 / 30 日
- 判定：**拒绝**

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过

## 边界

- Only four factors satisfy both P6 and R418 as of R439.
- This tests governance-qualified members, not an independently discovered strategy.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
