# R396 权重稳定性审计

不重新选权，只检查 R395 入选权重在 ±20% 单腿扰动和留一因子扰动下是否连续。

- 邻域数：25
- 通过数：23（92.0%）
- 稳定权重区间：是
- R395 基线检验年化：9.95% / Sharpe 0.880

## 邻域结果

| 扰动 | 训练年化 | 检验年化 | 检验 Sharpe | 检验 MDD | 3× 检验年化 | 通过 |
| --- | ---: | ---: | ---: | ---: | ---: | :---: |
| `selected` | 17.79% | 9.95% | 0.880 | -13.25% | 8.38% | 是 |
| `ext_alt_hs300_cash_flow_stability_4q_low_up` | 17.35% | 10.52% | 0.910 | -13.07% | 9.07% | 是 |
| `ext_alt_hs300_cash_flow_stability_4q_low_down` | 17.08% | 13.82% | 1.160 | -11.38% | 12.43% | 是 |
| `ext_alt_hs300_dividend_yield_up` | 17.43% | 9.88% | 0.870 | -13.31% | 8.32% | 是 |
| `ext_alt_hs300_dividend_yield_down` | 18.67% | 8.62% | 0.770 | -13.90% | 7.33% | 是 |
| `ext_alt_hs300_fund_holding_ratio_low_up` | 19.75% | 9.91% | 0.870 | -13.87% | 8.54% | 是 |
| `ext_alt_hs300_fund_holding_ratio_low_down` | 18.19% | 11.53% | 1.000 | -13.07% | 10.14% | 是 |
| `ext_alt_hs300_holder_top10_share_chg_yoy_up` | 15.10% | 9.90% | 0.870 | -14.90% | 8.52% | 是 |
| `ext_alt_hs300_holder_top10_share_chg_yoy_down` | 20.27% | 10.58% | 0.920 | -12.52% | 9.20% | 是 |
| `ext_alt_hs300_idio_vol_40d_low_up` | 18.66% | 8.82% | 0.800 | -12.59% | 7.49% | 是 |
| `ext_alt_hs300_idio_vol_40d_low_down` | 17.29% | 12.57% | 1.080 | -12.49% | 11.16% | 是 |
| `ext_alt_hs300_margin_chg_1m_low_up` | 17.89% | 9.31% | 0.820 | -13.24% | 8.02% | 是 |
| `ext_alt_hs300_margin_chg_1m_low_down` | 18.55% | 9.25% | 0.810 | -13.00% | 7.88% | 是 |
| `ext_alt_hs300_ocf_margin_4q_up` | 19.07% | 9.01% | 0.800 | -14.57% | 7.63% | 是 |
| `ext_alt_hs300_ocf_margin_4q_down` | 17.15% | 9.94% | 0.870 | -14.92% | 8.55% | 是 |
| `ext_alt_hs300_vol_of_vol_20d_low_up` | 18.84% | 7.78% | 0.710 | -13.31% | 6.42% | 否 |
| `ext_alt_hs300_vol_of_vol_20d_low_down` | 19.51% | 10.71% | 0.930 | -12.68% | 9.34% | 是 |
| `leave_out_ext_alt_hs300_cash_flow_stability_4q_low` | 16.67% | 11.66% | 1.000 | -12.97% | 10.29% | 是 |
| `leave_out_ext_alt_hs300_dividend_yield` | 17.24% | 11.35% | 0.980 | -12.19% | 9.93% | 是 |
| `leave_out_ext_alt_hs300_fund_holding_ratio_low` | 11.61% | 13.07% | 1.090 | -11.32% | 11.68% | 是 |
| `leave_out_ext_alt_hs300_holder_top10_share_chg_yoy` | 22.63% | 5.32% | 0.510 | -14.97% | 3.99% | 否 |
| `leave_out_ext_alt_hs300_idio_vol_40d_low` | 10.25% | 18.92% | 1.450 | -11.50% | 17.35% | 是 |
| `leave_out_ext_alt_hs300_margin_chg_1m_low` | 18.90% | 10.00% | 0.880 | -11.86% | 8.60% | 是 |
| `leave_out_ext_alt_hs300_ocf_margin_4q` | 17.46% | 11.83% | 1.020 | -13.34% | 10.42% | 是 |
| `leave_out_ext_alt_hs300_vol_of_vol_20d_low` | 17.94% | 14.50% | 1.260 | -11.06% | 13.08% | 是 |

## 结论边界

- 这是冻结后敏感性审计，不重新选择参数。
- 不生成订单、纸面目标或生产配置。
