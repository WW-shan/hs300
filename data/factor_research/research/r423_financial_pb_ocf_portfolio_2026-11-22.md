# R423 PB/OCF 财报腿组合确认

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 5.82% | -12.30% | -9.58% |
| replace_holder_pb | 15.79% | 26.72% | - | - | - | - | - | - |
| replace_holder_ocf_debt | 9.18% | 19.36% | - | - | - | - | - | - |
| replace_holder_and_ocf_margin_with_pb_ocf | 15.72% | 21.37% | - | - | - | - | - | - |

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

- R422 IC is statistical, not portfolio alpha.
- Only one replacement family is tested to avoid a new factor fishing loop.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- All variants must keep exactly eight legs; the two-factor variant drops two existing legs.
