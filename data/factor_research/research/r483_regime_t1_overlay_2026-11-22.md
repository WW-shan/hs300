# R483 T-1 市场状态叠加

本轮冻结 R480 的 8 腿组合，只使用前一交易日 regime 状态调整现金/权益暴露。

- 样本数：7
- 训练+验证合格：2
- 选中：`baseline`
- 判定：**拒绝**
- 检验年化：6.17%；Sharpe：0.494；MDD：-16.56%
- 近一年年化：7.92%；Sharpe：0.615；MDD：-10.19%
- 检验相对 HS300：+1.08%
- 近一年相对 HS300：+17.49%
- 检验 p05：-15.83%
- 近一年 p05：-8.73%

## 门槛

- training_platform_exists: 通过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- recent_annual_min: 通过
- test_bootstrap_positive: 未过
- recent_bootstrap_positive: 未过
- test_excess_hs300_positive: 通过
- no_leverage: 通过

## 边界

- regime_history is not a complete real-time vintage.
- Overlays only shift exposure between cash and the frozen sleeve.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
