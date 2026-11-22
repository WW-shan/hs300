# R514 R506 动态风险预算

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

- Overlays scale an already-simulated equity curve rather than rebalancing individual holdings.
- Trailing volatility and drawdown are not forecasts.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
