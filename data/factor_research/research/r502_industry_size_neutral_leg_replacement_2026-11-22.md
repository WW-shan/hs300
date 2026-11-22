# R502 行业+规模中性腿替换

本轮只审计 R501 暴露的两条弱中性腿；如果没有候选同时超过训练和验证，保留基准并拒绝。

- 基准验证年化：24.22%
- 可晋级替换：0
- 选中：`base`
- 判定：**拒绝**

## 门槛

- has_eligible_replacement: 未过
- selected_is_replacement: 未过
- validation_bootstrap_positive: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 未过
- no_leverage: 通过

## 边界

- The replacement candidates are selected from prior evidence and are not independent discoveries.
- The test/recent windows have been observed before.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
