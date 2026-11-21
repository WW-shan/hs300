# R415 防御腿替换确认

| 变体 | 腿数 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 8 | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 5.82% |
| drop_dividend | 7 | 9.23% | 27.74% | - | - | - | - |
| drop_holder | 7 | 17.63% | 20.95% | - | - | - | - |
| drop_dividend_and_holder | 6 | 16.21% | 27.88% | - | - | - | - |

- 选中：`baseline`
- 判定：**拒绝**

## 门槛

- selected_not_baseline: 未过
- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- beat_baseline_train: 未过
- beat_baseline_validation: 未过
- stress_3x_positive: 通过

## 边界

- Replacement legs are selected from observed weak-window attribution; this is not an independent discovery.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- The 8-leg cap is enforced.
