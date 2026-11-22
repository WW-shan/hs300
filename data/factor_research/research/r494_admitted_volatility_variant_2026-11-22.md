# R494 严格准入波动腿替换

本轮只把 R490 的 `vol_of_vol_20d_low` 替换为 R418 严格准入的 `vol_of_vol_60d_low`，其他四腿和执行参数冻结。

- 组合腿数：5
- 判定：**拒绝**
- 检验年化：6.67%；Sharpe：0.530；MDD：-19.86%
- 近一年年化：7.65%；Sharpe：0.610；MDD：-14.52%
- 检验相对 HS300：+1.86%
- 近一年相对 HS300：+17.25%
- 检验 p05：-17.33%
- 近一年 p05：-11.95%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- validation_sharpe_min: 通过
- validation_mdd_min: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 未过
- recent_annual_min: 通过
- stress_3x_positive: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- Only the volatility-stability leg changes; all other R490 parameters are frozen.
- The replacement is motivated by admission status, not by test performance.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
