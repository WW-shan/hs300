# R501 R499 行业+规模中性归因

本轮只做冻结腿的行业+规模中性诊断，不重新选样。

| 窗口 | 因子 | 原始IC | 中性IC | 差 | 方向保持 |
| --- | --- | ---: | ---: | ---: | --- |
| train | `ext_alt_hs300_cash_flow_stability_4q_low` | 0.02690447 | 0.01785749 | +0.009047 | 是 |
| train | `ext_alt_hs300_dividend_yield` | 0.04933703 | 0.03937786 | +0.009959 | 是 |
| train | `ext_alt_hs300_fund_holding_ratio_low` | 0.03659179 | 0.02194043 | +0.014651 | 是 |
| train | `ext_alt_hs300_idio_vol_40d_low` | 0.05192929 | 0.02706057 | +0.024869 | 是 |
| train | `value_quality` | 0.02838724 | 0.0351302 | -0.006743 | 是 |
| validation | `ext_alt_hs300_cash_flow_stability_4q_low` | 0.02017027 | 0.00307769 | +0.017093 | 是 |
| validation | `ext_alt_hs300_dividend_yield` | 0.03959994 | 0.02523095 | +0.014369 | 是 |
| validation | `ext_alt_hs300_fund_holding_ratio_low` | 0.03321207 | 0.01752278 | +0.015689 | 是 |
| validation | `ext_alt_hs300_idio_vol_40d_low` | 0.06836586 | 0.03316213 | +0.035204 | 是 |
| validation | `value_quality` | -0.00906244 | 0.0158611 | -0.024924 | 否 |
| test | `ext_alt_hs300_cash_flow_stability_4q_low` | 0.01913802 | 0.00323805 | +0.015900 | 是 |
| test | `ext_alt_hs300_dividend_yield` | 0.02744038 | 0.03138792 | -0.003948 | 是 |
| test | `ext_alt_hs300_fund_holding_ratio_low` | 0.00740353 | -0.00291353 | +0.010317 | 否 |
| test | `ext_alt_hs300_idio_vol_40d_low` | 0.02038558 | 0.02207636 | -0.001691 | 是 |
| test | `value_quality` | 0.02033181 | 0.02200712 | -0.001675 | 是 |
| recent | `ext_alt_hs300_cash_flow_stability_4q_low` | 0.03446599 | 0.01298287 | +0.021483 | 是 |
| recent | `ext_alt_hs300_dividend_yield` | 0.0589728 | 0.04501176 | +0.013961 | 是 |
| recent | `ext_alt_hs300_fund_holding_ratio_low` | 0.02586477 | 0.00223526 | +0.023630 | 是 |
| recent | `ext_alt_hs300_idio_vol_40d_low` | 0.0463117 | 0.03462605 | +0.011686 | 是 |
| recent | `value_quality` | 0.03299121 | 0.0343282 | -0.001337 | 是 |

## 边界

- Industry is a current snapshot, not a historical vintage.
- Neutral IC is a statistical diagnostic, not portfolio PnL.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
