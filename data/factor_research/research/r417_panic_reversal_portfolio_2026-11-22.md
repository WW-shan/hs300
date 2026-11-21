# R417 恐慌反转新因子组合确认

| 变体 | 腿数 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 8 | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 5.82% |
| replace_holder | 8 | 12.15% | 19.14% | - | - | - | - |
| replace_dividend | 8 | 10.93% | 35.72% | - | - | - | - |
| replace_ocf | 8 | 15.30% | 20.45% | - | - | - | - |

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
- recent_positive: 通过

## 边界

- New factor is research-only until separately registered and audited.
- The source statistical screen observed the test window; this is confirmation but not independent discovery.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- Only one replacement is allowed per variant; eight-leg cap enforced.
