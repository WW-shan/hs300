# R496 严格准入五因子参数平台

本轮冻结 R418 严格准入五因子，只搜索仓数、持有期、阈值和单日建仓上限。

- 样本数：216
- 训练+验证合格：216
- 稳定平台：16
- 选中：`p20_h50_e60_x50_c2`
- 判定：**拒绝**
- 检验年化：6.76%；Sharpe：0.600；MDD：-14.78%
- 近一年年化：8.45%；Sharpe：0.710；MDD：-10.67%
- 检验相对 HS300：+1.99%
- 近一年相对 HS300：+18.08%
- 检验 p05：-12.45%
- 近一年 p05：-9.94%

## 门槛

- training_platform_exists: 通过
- neighborhood_platform_pass: 通过
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

- The grid is finite and centered on the admitted set.
- Parameter neighborhoods are correlated, not independent trials.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
