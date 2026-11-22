# R485 cap=1 的持有期/暴露交互

本轮冻结 R484 的 8 腿、评分和每日 1 仓上限，只改变持有期与总暴露；训练+验证选择，检验+近一年确认。

- 样本数：12
- 训练+验证合格：6
- 选中：`h40_e100`
- 判定：**拒绝**
- 检验年化：10.08%；Sharpe：0.740；MDD：-13.64%
- 近一年年化：6.94%；Sharpe：0.560；MDD：-13.07%
- 检验相对 HS300：+7.02%
- 近一年相对 HS300：+16.51%
- 检验 p05：-11.20%
- 近一年 p05：-12.24%

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

- The grid is small and centered on R484; it is not an exhaustive optimizer.
- Lower exposure mechanically reduces both return and tail.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
