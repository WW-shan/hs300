# R509 Amihud 方向修正

- 只修正 `amihud_20d` 方向为 `high` 并重跑 5%/10% 两档。
- 训练+验证合格：0
- 选中：`base`
- 判定：**拒绝**

## 门槛

- has_eligible_amihud: 未过
- selected_is_amihud: 未过
- validation_bootstrap_positive: 未过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 未过
- no_leverage: 通过

## 边界

- This is a correction, not an independent discovery.
- Only two weights are tested for a single candidate.
- The test/recent windows have been observed before.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
