# R470 value_quality_safety 独立 sleeve 确认

本轮只检验 R452 的 `value_quality_safety`，不改变其统计证据，也不覆盖 R464 的组合结果。

- 样本数：54
- 训练+验证合格：54
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`vqs|p20|h30|e80|x30`
- 判定：**拒绝**
- 检验年化：17.07%
- 检验 Sharpe：1.140
- 检验 MDD：-21.53%
- 近一年年化：5.57%
- 检验 p05：-7.98%
- 近一年 p05：-21.91%

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- neighborhood_platform_pass: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 未过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 边界

- This is a standalone confirmation of an existing composite, not a new raw-data factor.
- R452 statistical evidence and R464 portfolio evidence are preserved.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
