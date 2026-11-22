# R481 入场/退出阈值邻域确认

本轮冻结 R480 的 8 腿与权重，只改变入场/退出评分；训练+验证选择，检验+近一年确认。

- 样本数：9
- 训练+验证合格：6
- 选中：`p80_x30`
- 判定：**拒绝**
- 检验年化：6.16%；Sharpe：0.490；MDD：-16.56%
- 近一年年化：7.89%；Sharpe：0.620；MDD：-10.19%
- 检验相对 HS300：+1.09%
- 近一年相对 HS300：+17.49%
- 检验 bootstrap p05：-14.96%
- 近一年 bootstrap p05：-8.20%

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

- Only entry/exit thresholds vary; all other parameters are frozen from R480.
- Test and recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
