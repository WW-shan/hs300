# R434 孤儿推荐 Amihud 组合确认

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 8.09% | 5.82% | -12.20% | -10.24% |
| amihud120_swap | 13.61% | 23.04% | - | - | - | - | - | - | - |

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

- The four variants are frozen from R433 orphan recommendations.
- Only one leg is replaced per variant; eight-leg cap enforced.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- Only amihud is both MC-recommended and R391 statistical-pass; the other three orphan recommendations are excluded as rejected.
