# R476 因子集扩容与复合层验证

本轮不再让 8 参数策略直接承载扩容：先把 9 腿按完整方向表物化成日频 rank composite，再交给单评分腿策略回测。

- 样本数：6
- 训练+验证合格：5
- 验证 bootstrap 正尾：0
- 选中：`base_plus_inventory_growth_minus_cogs_growth_at_20`
- 组合腿数：9
- 候选权重：20%
- 判定：**拒绝**
- 检验年化：3.11%
- 检验 Sharpe：0.290
- 检验 MDD：-20.08%
- 近一年年化：11.10%
- 检验 p05：-18.33%
- 近一年 p05：-8.25%
- 3× 检验年化：2.10%
- 5× 检验年化：0.03%（仅诊断）

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 未过
- test_annual_min: 通过
- test_sharpe_min: 未过
- test_mdd_min: 未过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_total_p05_positive: 未过
- recent_total_p05_positive: 未过
- expanded_leg_count: 通过
- no_leverage: 通过

## 边界

- The composite layer changes combination, not raw factor formulas.
- R402 used a fixed 8-factor cap; this round explicitly supersedes that implementation limit.
- Current HS300 membership and current industry classification retain survivorship bias.
- The test and recent windows have been observed before.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
