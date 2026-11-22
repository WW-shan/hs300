# R473 营运效率增量 sleeve 验证

本轮把 R468 的营运效率因子作为 sleeve 加入 R463 四因子基线，只改变营运效率权重，训练+验证选样，检验+近一年确认。

- 样本数：3
- 训练+验证合格：0
- 验证 bootstrap 正尾：0
- 选中：`four_plus_op_10`
- 判定：**拒绝**
- 检验年化：8.86%
- 检验 Sharpe：0.720
- 检验 MDD：-15.18%
- 近一年年化：7.25%
- 检验 p05：-11.24%
- 近一年 p05：-12.92%
- 相对四因子检验增量：-3.48%
- 相对四因子近一年增量：-4.30%

## 门槛

- training_platform_exists: 未过
- validation_bootstrap_positive: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- increment_vs_four_test_positive: 未过
- increment_vs_four_recent_positive: 未过
- no_leverage: 通过

## 边界

- This is an incremental sleeve test, not a new factor formula.
- The operating-efficiency factor starts in 2023 and has lower early exposure.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
