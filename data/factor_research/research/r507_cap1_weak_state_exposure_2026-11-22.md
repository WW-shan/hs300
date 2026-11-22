# R507 Cap-1 弱市暴露闸门

- 弱市暴露：100%
- 判定：**拒绝**
- 验证 p05：4.32%
- 检验年化：12.88%；Sharpe：1.028；MDD：-10.27%
- 近一年年化：15.49%；Sharpe：1.174；MDD：-8.89%
- 检验 p05：-5.18%
- 近一年 p05：-4.06%

## 门槛

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

- The overlay scales an already-simulated equity curve rather than rebalancing individual holdings.
- Weak-state labels are historical and not a full real-time vintage.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
