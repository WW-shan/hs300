# R499 弱市价值质量降权确认

本轮固定 R463 组合，只在 R498 冻结的弱市状态下降低 `value_quality` 权重。

- 弱市 `value_quality` 权重：5.0%
- 弱市其余四腿权重：各 23.75%
- 判定：**拒绝**
- 检验年化：23.79%；Sharpe：1.710；MDD：-11.42%
- 近一年年化：16.05%；Sharpe：1.180；MDD：-12.53%
- 检验相对 HS300：+28.45%
- 近一年相对 HS300：+25.91%
- 检验 p05：5.10%
- 近一年 p05：-7.49%

## 门槛

- training_platform_exists: 未过
- validation_bootstrap_positive: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_bootstrap_positive: 通过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- The weak-state threshold comes from historical train+validation data and may not persist.
- Only weak-state value_quality weight changes; all other execution parameters are frozen.
- The three candidates are correlated and are not independent trials.
- The test/recent windows have been observed before.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
