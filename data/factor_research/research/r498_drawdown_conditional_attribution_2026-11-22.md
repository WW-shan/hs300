# R498 回撤簇条件归因

本轮冻结 R463 五腿组合，只用训练+验证期估计弱市阈值，检验和近一年只做确认。

- 弱市阈值：score <= 39.00，或 breadth < 35%
- 判定：**未通过**

## 窗口

| 窗口 | 弱市天数 | 弱市日均 | 非弱市日均 | 弱市累计 | 非弱市累计 |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 220 | 0.1469% | 0.0562% | 36.74% | 15.01% |
| validation | 119 | 0.0144% | 0.1148% | 0.88% | 13.92% |
| test | 148 | 0.0823% | 0.1083% | 12.29% | 25.76% |
| recent | 129 | 0.0416% | 0.0600% | 4.99% | 7.59% |

## 各腿弱市贡献

| 因子 | 训练弱市 | 验证弱市 | 检验弱市 | 近一年弱市 | 稳定性 |
| --- | ---: | ---: | ---: | ---: | --- |
| `ext_alt_hs300_cash_flow_stability_4q_low` | 0.029467 | 0.011587 | 0.021622 | 0.025312 | 是 |
| `ext_alt_hs300_dividend_yield` | 0.044616 | 0.021650 | 0.041823 | 0.056125 | 是 |
| `ext_alt_hs300_fund_holding_ratio_low` | 0.035064 | 0.034609 | 0.014491 | 0.020434 | 是 |
| `ext_alt_hs300_idio_vol_40d_low` | 0.039888 | 0.028266 | 0.038055 | 0.051372 | 是 |
| `value_quality` | 0.036244 | -0.016829 | 0.018931 | 0.023262 | 否 |

## 门槛

- weak_days_train_min: 通过
- weak_days_validation_min: 通过
- weak_days_test_min: 通过
- weak_days_recent_min: 通过
- minimum_leg_observations: 通过
- stable_leg_sign_across_windows: 未过
- no_leverage: 通过

## 边界

- This is descriptive attribution and cannot promote R463.
- Weak-state labels are based on lagged market-level data and are not a complete real-time vintage.
- Leg contribution is a rank/return covariance proxy, not actual executed PnL attribution.
- The test/recent windows have been observed before and are confirmation-only.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
