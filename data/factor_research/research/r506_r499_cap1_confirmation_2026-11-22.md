# R506 R499 Cap-1 独立确认

- 本轮不重新选样，只确认 `cap_1`。
- 判定：**拒绝**
- 验证 p05：4.68%
- 检验年化：12.84%；Sharpe：1.030；MDD：-10.27%
- 近一年年化：15.42%；Sharpe：1.170；MDD：-8.89%
- 检验 p05：-4.83%
- 近一年 p05：-3.61%

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

- This is a correction confirmation, not a new independent selection.
- Cap 1 was identified from the R505 train+validation table.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
