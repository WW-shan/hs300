# R493 资金/关注度因子增补

本轮在 R490 的 5 腿核心上加入 1 个龙虎榜资金因子，训练+验证选样，检验+近一年确认。

- 样本数：9
- 训练+验证合格：7
- 选中：`ext_alt_hs300_lhb_inst_net_buy_ratio_20d_at_10`
- 判定：**拒绝**
- 检验年化：13.56%；Sharpe：0.990；MDD：-12.72%
- 近一年年化：7.44%；Sharpe：0.590；MDD：-11.49%
- 检验相对 HS300：+12.35%
- 近一年相对 HS300：+17.03%
- 检验 p05：-7.64%
- 近一年 p05：-11.50%

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

- Flow factors are event/sparse and may have unstable history.
- All candidates share the same frozen core and are correlated.
- Test/recent windows are reused confirmation windows.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
