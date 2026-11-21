# R432 MC 参数族交互确认

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 8.09% | 5.82% | -12.66% | -10.04% |
| mc_idio20 | 15.78% | 20.93% | - | - | - | - | - | - | - |
| mc_volvol60 | 15.07% | 23.55% | - | - | - | - | - | - | - |
| mc_both_idio20_volvol60 | 15.21% | 21.31% | - | - | - | - | - | - | - |

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

- Only two MC-tunable families exist in the current eight-leg set.
- The other six legs are fixed-definition and have no window parameter.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
