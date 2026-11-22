# R487 持有期/重入周期审计

本轮冻结 R484 的 8 腿、权重、评分和 cap=1，只改变最大持有期。

- 样本数：7
- 训练+验证合格：2
- 选中：`h40`
- 判定：**拒绝**
- 检验年化：10.08%；Sharpe：0.740；MDD：-13.64%
- 近一年年化：6.94%；Sharpe：0.560；MDD：-13.07%
- 检验相对 HS300：+7.02%
- 近一年相对 HS300：+16.51%
- 检验 p05：-10.61%
- 近一年 p05：-11.99%

## 门槛

- training_platform_exists: 通过
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

- This is a hold/re-entry horizon audit, not a new factor.
- Shorter horizons can increase turnover and costs.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
