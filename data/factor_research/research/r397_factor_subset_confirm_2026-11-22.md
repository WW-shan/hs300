# R397 因子子集与组合参数确认

训练段只用于筛选 9/9 参数平台；下面所有子集在检验段一次性确认，未使用检验段重新选参。

> **口径警告：** `holding_days=20/30/40` 实际未生效，策略固定为 30 日；只有持仓数 10/20/30 是有效的。持有期需下一轮修正。

- 候选子集：33
- 检验通过：15

| 子集 | 仓数 | 持有期 | 训练年化 | 检验年化 | 检验 Sharpe | 检验 MDD | 3× 检验年化 | 通过 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio | 10 | 20 | 25.50% | 11.10% | 0.860 | -12.62% | 9.76% | 是 |
| cash_flow_stability_4q, fund_holding_ratio, idio_vol_40d, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 24.62% | 6.88% | 0.620 | -14.63% | 5.62% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, ocf_margin_4q | 10 | 20 | 23.48% | 7.26% | 0.580 | -14.79% | 5.96% | 否 |
| cash_flow_stability_4q, fund_holding_ratio, idio_vol_40d | 10 | 20 | 22.54% | 0.80% | 0.130 | -18.75% | -0.47% | 否 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d, ocf_margin_4q, vol_of_vol_20d | 20 | 20 | 22.42% | 8.38% | 0.720 | -12.81% | 7.09% | 是 |
| dividend_yield, fund_holding_ratio, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 21.70% | 10.22% | 0.800 | -15.22% | 8.77% | 是 |
| dividend_yield, idio_vol_40d, vol_of_vol_20d | 20 | 20 | 21.61% | 5.09% | 0.470 | -16.60% | 3.80% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, vol_of_vol_20d | 20 | 20 | 21.56% | 3.83% | 0.380 | -15.79% | 2.59% | 否 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d | 10 | 20 | 21.51% | 4.22% | 0.400 | -15.35% | 2.90% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, margin_chg_1m, vol_of_vol_20d | 20 | 20 | 21.38% | 4.66% | 0.430 | -14.68% | 3.41% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, ocf_margin_4q, vol_of_vol_20d | 20 | 20 | 21.27% | 8.19% | 0.690 | -13.48% | 6.86% | 是 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, margin_chg_1m, ocf_margin_4q, vol_of_vol_20d | 20 | 20 | 21.01% | 7.84% | 0.660 | -14.14% | 6.55% | 是 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, margin_chg_1m | 10 | 20 | 20.85% | 11.08% | 0.870 | -16.04% | 9.64% | 是 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d, margin_chg_1m | 10 | 20 | 20.83% | 10.45% | 0.850 | -13.99% | 9.02% | 是 |
| dividend_yield, idio_vol_40d, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 20.74% | 2.20% | 0.240 | -16.20% | 0.92% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d | 10 | 20 | 20.57% | 3.03% | 0.300 | -14.14% | 1.67% | 否 |
| dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d, vol_of_vol_20d | 10 | 20 | 20.53% | 5.65% | 0.510 | -18.75% | 4.31% | 否 |
| cash_flow_stability_4q, dividend_yield, holder_top10_share_chg_yoy, idio_vol_40d, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 20.46% | 7.30% | 0.640 | -15.49% | 5.98% | 是 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 20.18% | 5.26% | 0.500 | -15.20% | 3.96% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d, vol_of_vol_20d | 30 | 20 | 20.07% | 5.61% | 0.530 | -13.95% | 4.26% | 否 |
| dividend_yield, idio_vol_40d, margin_chg_1m, ocf_margin_4q, vol_of_vol_20d | 10 | 20 | 19.97% | 2.57% | 0.260 | -17.15% | 1.31% | 否 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d, margin_chg_1m, ocf_margin_4q, vol_of_vol_20d | 10 | 20 | 19.77% | 6.34% | 0.530 | -13.94% | 5.08% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, margin_chg_1m, ocf_margin_4q, vol_of_vol_20d | 10 | 20 | 19.55% | 7.90% | 0.610 | -14.91% | 6.53% | 是 |
| dividend_yield, fund_holding_ratio, idio_vol_40d, vol_of_vol_20d | 30 | 20 | 19.37% | 3.75% | 0.370 | -15.69% | 2.40% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d | 10 | 20 | 19.31% | 7.90% | 0.660 | -13.31% | 6.63% | 是 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d, margin_chg_1m | 30 | 20 | 19.26% | 5.66% | 0.530 | -15.08% | 4.36% | 否 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d, ocf_margin_4q, vol_of_vol_20d | 10 | 20 | 19.12% | 13.45% | 1.000 | -10.53% | 12.11% | 是 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, idio_vol_40d, margin_chg_1m, ocf_margin_4q | 20 | 20 | 18.50% | 8.92% | 0.750 | -12.65% | 7.56% | 是 |
| cash_flow_stability_4q, idio_vol_40d, margin_chg_1m, ocf_margin_4q, vol_of_vol_20d | 20 | 20 | 18.39% | 8.08% | 0.700 | -10.96% | 6.70% | 是 |
| cash_flow_stability_4q, dividend_yield, fund_holding_ratio, holder_top10_share_chg_yoy, margin_chg_1m, vol_of_vol_20d | 10 | 20 | 17.80% | 7.06% | 0.590 | -15.92% | 5.73% | 否 |
| cash_flow_stability_4q, dividend_yield, idio_vol_40d, margin_chg_1m, ocf_margin_4q | 10 | 20 | 17.18% | 8.38% | 0.680 | -12.58% | 7.07% | 是 |
| fund_holding_ratio, holder_top10_share_chg_yoy, idio_vol_40d, vol_of_vol_20d | 20 | 20 | 16.13% | 11.25% | 0.970 | -13.81% | 9.96% | 是 |
| dividend_yield, fund_holding_ratio, idio_vol_40d, margin_chg_1m, ocf_margin_4q | 20 | 20 | 14.44% | 6.70% | 0.620 | -13.87% | 5.52% | 否 |

## 结论边界

- 子集来自预登记训练平台，不是新增因子。
- 检验段仅确认，不再选择。
- 不生成订单、纸面目标或生产配置。
