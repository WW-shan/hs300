# R443 四因子尾部控制叠加

| 风控 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 20.98% | 21.07% | - | - | - | - | - | - | - |
| stop_loss_10 | 22.05% | 24.52% | 4.72% | 0.420 | -15.39% | 6.79% | 3.46% | -17.79% | -11.56% |
| stop_loss_15 | 27.73% | 21.83% | - | - | - | - | - | - | - |
| trailing_stop_10 | 23.91% | 10.39% | - | - | - | - | - | - | - |
| trailing_stop_15 | 26.19% | 13.55% | - | - | - | - | - | - | - |
| stop10_trailing10 | 21.69% | 16.92% | - | - | - | - | - | - | - |

- 选中：`stop_loss_10`
- 判定：**拒绝**

## 门槛

- selected_not_baseline: 通过
- train_annual_min: 通过
- validation_annual_min: 通过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- stress_3x_positive: 通过

## 边界

- Overlays are selected on train+validation only.
- Test/recent tail is confirmation only.
- The test window has been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
