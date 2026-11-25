# R542 R539C 最近一年失败归因

> 本轮只诊断冻结候选，不选新因子、不调权重、不产生 active version。

## 摘要

- Full 2x：总收益 95.68%，Sharpe 1.14，MDD -19.08%。
- Recent 2x：总收益 -0.91%，Sharpe -0.01，MDD -18.81%。
- 同期沪深300：full 2.57%，recent -9.38%。
- 同期 PIT 成分等权：full 56.25%，recent -0.61%。
- Recent 对沪深300超额：8.47%。
- Recent 对 PIT 等权超额：-0.30%。
- Recent 组合 beta（沪深300）：0.014。
- 止损对 recent 的收益差：6.11%；对 full：7.33%。

## 因子 IC

| 因子 | 方向 | train IC | validation IC | recent IC | recent 中性 IC | recent-train | recent 五分位 spread |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `amount_level` | low | 0.0410 | 0.0098 | 0.0333 | 0.0168 | -0.0076 | 0.34% |
| `bollinger_width` | low | 0.0502 | 0.0285 | 0.0336 | 0.0143 | -0.0166 | 0.01% |
| `dividend_yield_quantile` | high | 0.0541 | 0.0226 | 0.0597 | 0.0280 | +0.0056 | - |
| `fund_count_quantile` | high | 0.0233 | -0.0119 | -0.0125 | -0.0097 | -0.0359 | - |
| `fund_holding_ratio_absolute` | high | 0.0428 | 0.0095 | 0.0273 | -0.0009 | -0.0155 | - |
| `lhb_inst_net_buy_event` | high | 0.0043 | -0.0062 | -0.0042 | 0.0083 | -0.0085 | 0.04% |
| `lhb_net_buy_holding` | high | -0.0078 | -0.0120 | -0.0169 | -0.0003 | -0.0091 | -0.40% |
| `market_down_beta` | high | 0.0378 | -0.0191 | 0.0249 | 0.0119 | -0.0130 | -0.00% |
| `max_return` | low | 0.0643 | 0.0350 | 0.0597 | 0.0427 | -0.0046 | 0.34% |
| `repurchase_event_decay` | high | -0.0060 | -0.0075 | -0.0101 | -0.0019 | -0.0041 | - |
| `repurchase_holding` | high | -0.0105 | -0.0081 | -0.0154 | 0.0021 | -0.0049 | - |
| `turnover_level` | low | 0.0646 | 0.0188 | 0.0609 | 0.0246 | -0.0037 | 0.53% |
| `vol_of_vol` | high | 0.0486 | 0.0271 | 0.0532 | 0.0320 | +0.0046 | 0.28% |

## 留一法边际贡献

`贡献 = baseline 总收益 - 去掉该因子后的总收益`，正数表示该因子有正贡献。

| 因子 | recent 贡献 | full 贡献 | single recent | LOO recent |
| --- | ---: | ---: | ---: | ---: |
| `dividend_yield_quantile` | +4.75% | +30.83% | -6.17% | -5.66% |
| `turnover_level` | +4.54% | +35.09% | 19.95% | -5.45% |
| `max_return` | +3.99% | +18.90% | -3.96% | -4.90% |
| `vol_of_vol` | +3.84% | -7.04% | 3.18% | -4.75% |
| `market_down_beta` | +3.66% | +10.19% | -24.56% | -4.57% |
| `repurchase_event_decay` | +1.49% | -7.58% | -16.82% | -2.40% |
| `repurchase_holding` | +1.49% | -7.77% | -25.48% | -2.40% |
| `lhb_inst_net_buy_event` | +0.00% | +10.60% | -26.74% | -0.91% |
| `lhb_net_buy_holding` | +0.00% | -7.16% | -24.54% | -0.91% |
| `fund_count_quantile` | -0.48% | +14.39% | -19.06% | -0.43% |
| `amount_level` | -1.45% | +18.64% | -2.92% | 0.54% |
| `fund_holding_ratio_absolute` | -2.97% | -6.22% | -4.70% | 2.06% |
| `bollinger_width` | -4.55% | +19.02% | -0.09% | 3.64% |

## 风险、交易和回撤

- 最近一年交易：53，正/负：23/30。
- 总 PnL：-9050.17；最差单笔：-11611.77。
- 平均/中位持有天数：48.47/60.00。
- 平均规模分位：49.28%。
- 最大回撤区间：2025-11-12 → 2026-06-30，-18.81%，恢复日 未恢复。
- 出场原因：{"end": 10, "max_hold": 33, "stop_loss": 10}。

### 行业 PnL

| 行业 | PnL |
| --- | ---: |
| 交通运输 | +22166.99 |
| 银行 | +18847.00 |
| 公用事业 | +18071.85 |
| 机械设备 | +2031.60 |
| 建筑装饰 | -6004.80 |
| 通信 | -10831.68 |
| 汽车 | -11607.06 |
| 食品饮料 | -16394.06 |
| 医药生物 | -25330.01 |

### 因子暴露（持仓方向分位均值，50%=中性）

| 因子 | 持仓分位 |
| --- | ---: |
| `amount_level` | 86.46% |
| `bollinger_width` | 85.67% |
| `dividend_yield_quantile` | 46.34% |
| `fund_count_quantile` | 65.00% |
| `fund_holding_ratio_absolute` | 71.96% |
| `lhb_inst_net_buy_event` | 49.54% |
| `lhb_net_buy_holding` | 48.93% |
| `market_down_beta` | 81.92% |
| `max_return` | 89.32% |
| `repurchase_event_decay` | 50.95% |
| `repurchase_holding` | 50.69% |
| `turnover_level` | 88.30% |
| `vol_of_vol` | 83.98% |

## 市场 regime

- 覆盖：{'strategy_days': 258, 'benchmark_days': 2153, 'regime_days': 2132, 'matched_days': 246, 'regime_start': '2018-01-02', 'regime_end': '2026-10-21'}。

| State | 天数 | 策略日均 | 基准日均 | 日均超额 | 策略累计 | 基准累计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| lean_strong | 62 | 0.1048% | 0.8480% | -0.7432% | 6.53% | 68.59% |
| lean_weak | 55 | -0.0452% | -0.1843% | +0.1392% | -2.59% | -9.72% |
| range | 44 | -0.0320% | 0.1313% | -0.1632% | -1.52% | 5.88% |
| strong | 19 | 0.2966% | 1.4932% | -1.1966% | 5.74% | 32.46% |
| weak | 66 | -0.1526% | -1.2868% | +1.1342% | -9.85% | -57.56% |

## 月度收益（最近一年）

| 月份 | 策略 | 沪深300 | PIT 等权 | 对沪深300 | 对等权 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-10 | 0.05% | 2.24% | 1.56% | -2.19% | -1.51% |
| 2025-11 | 0.46% | -2.46% | -2.61% | +2.92% | +3.07% |
| 2025-12 | -2.27% | 2.28% | 3.84% | -4.55% | -6.10% |
| 2026-01 | -3.61% | 1.65% | 3.19% | -5.26% | -6.81% |
| 2026-02 | 2.69% | 0.09% | 2.02% | +2.60% | +0.67% |
| 2026-03 | 0.60% | -5.53% | -6.92% | +6.14% | +7.52% |
| 2026-04 | -4.17% | 8.03% | 5.50% | -12.20% | -9.67% |
| 2026-05 | -1.86% | 1.76% | -1.64% | -3.62% | -0.22% |
| 2026-06 | -9.66% | 1.78% | 0.19% | -11.45% | -9.85% |
| 2026-07 | 14.11% | -7.86% | -0.10% | +21.97% | +14.21% |
| 2026-08 | 0.72% | 0.80% | 1.33% | -0.08% | -0.61% |
| 2026-09 | 0.73% | -5.78% | -4.51% | +6.51% | +5.24% |
| 2026-10 | 3.28% | -8.54% | -6.33% | +11.82% | +9.61% |
| 2026-11 | -0.28% | 3.21% | 4.92% | -3.49% | -5.20% |

## 结论边界

- 诊断完成：True。
- 结论：`rejected`。
- 最近一年绝对收益为负，但同期沪深300和 PIT 等权也不同程度下跌；不能把相对抗跌误写成绝对盈利。
- 留一法只代表组合边际贡献，不能单独证明某个因子的因果 alpha。

## 已知边界

- This is a diagnostic attribution of the frozen R539C candidate, not a new selection round.
- Leave-one-out contribution is a portfolio marginal effect, not causal attribution.
- The equal-weight PIT benchmark is a costless diagnostic approximation.
- The local regime history ends before the frozen end date; unmatched days are reported rather than extrapolated.
- R539C recent-year results were observed repeatedly and are not an independent discovery sample.
- No leverage, orders, paper targets, production enablement or active version are produced.
