# R500 R499 近一年尾部归因

本轮不重新选样，只拆解 R499 冻结样本的检验和近一年尾部。

| 窗口 | 绝对 p05 | 超额 p05 | 弱市日均 | 非弱市日均 | 最差月份 |
| --- | ---: | ---: | ---: | ---: | --- |
| test | 5.47% | -19.96% | 0.0533% | 0.1120% | 2026-06 -8.94% |
| recent | -7.86% | -14.69% | 0.0506% | 0.0752% | 2026-06 -11.67% |

## 月度收益

| 窗口 | 月份 | 收益 |
| --- | --- | ---: |
| test | 2025-05 | 4.01% |
| test | 2025-06 | 1.88% |
| test | 2025-07 | 2.40% |
| test | 2025-08 | 1.28% |
| test | 2025-09 | 1.89% |
| test | 2025-10 | 5.41% |
| test | 2025-11 | -1.56% |
| test | 2025-12 | -2.25% |
| test | 2026-01 | -2.14% |
| test | 2026-02 | 4.25% |
| test | 2026-03 | 3.10% |
| test | 2026-04 | -0.59% |
| test | 2026-05 | -0.14% |
| test | 2026-06 | -8.94% |
| test | 2026-07 | 13.46% |
| test | 2026-08 | -1.13% |
| test | 2026-09 | -0.70% |
| test | 2026-10 | 4.36% |
| test | 2026-11 | -0.77% |
| recent | 2025-10 | 2.06% |
| recent | 2025-11 | 0.22% |
| recent | 2025-12 | -3.24% |
| recent | 2026-01 | -0.14% |
| recent | 2026-02 | 3.75% |
| recent | 2026-03 | 3.38% |
| recent | 2026-04 | -1.87% |
| recent | 2026-05 | 1.94% |
| recent | 2026-06 | -11.67% |
| recent | 2026-07 | 12.91% |
| recent | 2026-08 | -2.17% |
| recent | 2026-09 | -0.84% |
| recent | 2026-10 | 4.05% |
| recent | 2026-11 | -0.59% |

## 近一年亏损月因子 IC

| 因子 | 近一年亏损月 IC | 有效日期 |
| --- | ---: | ---: |
| `ext_alt_hs300_cash_flow_stability_4q_low` | 0.01391404 | 127 |
| `ext_alt_hs300_dividend_yield` | -0.06483311 | 127 |
| `ext_alt_hs300_fund_holding_ratio_low` | -0.03156308 | 127 |
| `ext_alt_hs300_idio_vol_40d_low` | -0.06777908 | 127 |
| `value_quality` | -0.00950642 | 127 |

## 边界

- This diagnostic cannot promote R499.
- Monthly attribution is descriptive and does not prove causation.
- Weak-state labels reuse R498's historical threshold.
- The test/recent windows have been observed before.
- 5x cost remains warning-only.
- No leverage and no production activation.
