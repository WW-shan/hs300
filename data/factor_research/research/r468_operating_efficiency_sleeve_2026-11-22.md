# R468 营运效率因子独立 sleeve 确认

本轮只检验 R384 两个统计/增量通过的营运效率因子，不覆盖 R384 的组合替换失败记录。

- 样本数：108
- 训练+验证合格：27
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`inventory_growth_minus_cogs_growth|p10|h20|e70|x30`
- 判定：**拒绝**
- 检验年化：37.81%
- 检验 Sharpe：1.730
- 检验 MDD：-12.37%
- 近一年年化：7.64%
- 检验 p05：2.20%
- 近一年 p05：-19.75%

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- neighborhood_platform_pass: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 通过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 边界

- This is a standalone confirmation of two existing PIT financial factors, not a new formula.
- R384 replacement-portfolio failures are preserved.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
