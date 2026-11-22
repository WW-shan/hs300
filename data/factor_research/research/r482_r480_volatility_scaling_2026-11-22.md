# R482 R480 组合波动率缩放

本轮冻结 R480 的 8 腿组合，只对已实现收益序列做无杠杆现金/权益缩放；训练+验证选择，检验+近一年确认。

- 样本数：5
- 训练+验证合格：2
- 选中：`drawdown_guard`
- 判定：**拒绝**
- 检验年化：1.55%；Sharpe：0.185；MDD：-17.41%
- 近一年年化：7.59%；Sharpe：0.594；MDD：-10.21%
- 检验相对 HS300：-5.75%
- 近一年相对 HS300：+17.15%
- 检验 bootstrap p05：-18.64%
- 近一年 bootstrap p05：-8.99%
- 权重范围：0.00..1.00

## 门槛

- training_platform_exists: 通过
- test_annual_min: 未过
- test_sharpe_min: 未过
- test_mdd_min: 通过
- recent_annual_min: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 未过
- no_leverage: 通过

## 边界

- Overlays only shift exposure between cash and the frozen R480 sleeve; they cannot improve stock selection.
- Trailing volatility and drawdown estimates are not forecasts.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
