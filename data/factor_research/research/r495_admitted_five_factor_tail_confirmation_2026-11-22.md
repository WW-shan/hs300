# R495 严格准入五因子尾部确认

本轮只使用 R418 严格准入的五个因子，执行参数沿用 R419；训练+验证、检验、近一年和 bootstrap 全部确认。

- 组合腿数：5
- 判定：**拒绝**
- 检验年化：14.33%；Sharpe：1.110；MDD：-10.51%
- 近一年年化：3.18%；Sharpe：0.300；MDD：-15.14%
- 检验相对 HS300：+13.54%
- 近一年相对 HS300：+12.65%
- 检验 p05：-4.37%
- 近一年 p05：-15.16%

## 门槛

- train_annual_min: 通过
- validation_annual_min: 通过
- validation_sharpe_min: 通过
- validation_mdd_min: 通过
- validation_bootstrap_positive: 未过
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

- The set is selected from prior admission research, not a fresh holdout.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
