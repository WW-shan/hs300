# R489 去相关 6–8 腿子集搜索

本轮从 R480 的 8 腿库中枚举 6–8 腿子集，训练+验证选样，检验+近一年确认。

- 子集总数：37
- 进入 bootstrap 的 finalist：37
- 训练+验证合格：24
- 选中：`subset_6_ext_alt_hs300_cash_flow_stability_4q_low_ext_alt_hs300_dividend_yield_ext_alt_hs300_fund_holding_ratio_low_ext_alt_hs300_idio_vol_40d_low_ext_alt_hs300_vol_of_vol_20d_low_pb_latest`
- 腿数：6
- 判定：**拒绝**
- 检验年化：15.62%；Sharpe：1.130；MDD：-13.24%
- 近一年年化：9.19%；Sharpe：0.720；MDD：-13.25%
- 检验相对 HS300：+15.53%
- 近一年相对 HS300：+18.84%
- 检验 p05：-6.00%
- 近一年 p05：-11.20%

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

- All subsets are drawn from the same frozen 8-leg library.
- The search is deterministic but still a multiple-comparison problem.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
