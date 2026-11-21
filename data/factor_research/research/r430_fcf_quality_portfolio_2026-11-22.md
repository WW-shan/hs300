# R430 FCF/OCF 收入质量组合确认

| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 8.09% | 5.82% | -12.49% | -9.97% |
| replace_holder_fcf | 16.67% | 22.90% | - | - | - | - | - | - | - |
| replace_holder_ocf_revenue | 15.75% | 23.73% | - | - | - | - | - | - | - |
| replace_holder_ocf_margin_with_fcf_ocf | 15.62% | 22.90% | - | - | - | - | - | - | - |

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

- R429 IC is statistical, not portfolio alpha.
- Only three variants are tested to avoid a new fishing loop.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
