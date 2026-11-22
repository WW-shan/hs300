# R508 R499 低权重增量因子筛查

- 候选变体：16
- 训练+验证合格：0
- 选中：`base`
- 判定：**拒绝**

## 门槛

- has_eligible_adjunct: 未过
- selected_is_adjunct: 未过
- validation_bootstrap_positive: 未过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 未过
- no_leverage: 通过

## 边界

- Adjunct weights are a small pre-registered grid, not an exhaustive optimizer.
- Candidates were already statistically screened and are not independent discoveries.
- The test/recent windows have been observed before.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
