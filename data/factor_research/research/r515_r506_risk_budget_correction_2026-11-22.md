# R515 R506 动态风险预算确认修正

- 选中：`drawdown_10_cash`
- 判定：**拒绝**

## 门槛

- training_platform_exists: 通过
- validation_bootstrap_positive: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- This corrects a missing confirmation branch in R514, not the overlay definitions.
- Overlays scale an already-simulated equity curve.
- The test/recent windows have been observed before.
- No leverage and no production activation.
