# R474 恐慌反转情绪 sleeve 独立确认

本轮只检验 R416 的 `new_panic_reversal_5d` 独立 sleeve，不覆盖 R417 的组合替换结论。

- 样本数：486
- 训练+验证合格：0
- 验证 bootstrap 正尾：0
- 邻域稳定：0
- 选中：`panic|lb5|vr1.2|p10|h5|e60|x30`
- 判定：**拒绝**
- 检验年化：-28.52%
- 检验 Sharpe：-2.520
- 检验 MDD：-41.52%
- 近一年年化：-37.29%
- 检验 p05：-55.74%
- 近一年 p05：-53.10%

## 门槛

- training_platform_exists: 未过
- validation_bootstrap_positive: 未过
- neighborhood_platform_pass: 未过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- recent_annual_min: 未过
- stress_3x_positive: 未过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- no_leverage: 通过

## 边界

- This is a standalone confirmation of a research-only sentiment interaction factor.
- R416/R417 evidence is preserved.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
