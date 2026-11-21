# R412 财报基础字段增量筛查

| 因子 | 方向 | 覆盖率 | 边际IC | NW p | q | 相关 | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| pb_latest | low | 72.62% | 0.001751 | 0.02934788 | 0.1778 | 0.632992 | statistical_rejected |
| roe_latest | high | 72.65% | -0.000419 | 0.61120537 | 0.7470 | 0.113042 | statistical_rejected |
| gross_margin_latest | high | 61.17% | -0.000798 | 0.40569125 | 0.7011 | 0.129788 | statistical_rejected |
| net_margin_latest | high | 72.75% | 0.000568 | 0.42744861 | 0.7011 | 0.10168 | statistical_rejected |
| revenue_yoy_latest | high | 72.75% | 0.000181 | 0.845146 | 0.8451 | 0.306695 | statistical_rejected |
| net_income_yoy_latest | high | 72.75% | 0.00035 | 0.71892591 | 0.7908 | 0.208713 | statistical_rejected |
| ocf_ni | high | 56.22% | 0.001086 | 0.03232703 | 0.1778 | 0.232709 | statistical_rejected |
| accrual | low | 60.20% | 0.001035 | 0.0796769 | 0.2921 | 0.146094 | statistical_rejected |
| debt_ratio_latest | low | 72.83% | -0.000675 | 0.52320264 | 0.7194 | 0.27098 | statistical_rejected |
| ocf_debt | high | 60.18% | 0.000613 | 0.44616584 | 0.7011 | 0.135514 | statistical_rejected |
| cash_to_assets_latest | high | 72.84% | -0.000927 | 0.20246047 | 0.5568 | 0.360303 | statistical_rejected |

## 边界

- 这是 5 日增量 IC 统计初筛，不是组合收益或交易结论。
- 财报字段使用公告日下一交易日生效的 PIT 面板。
- 当前 HS300 成分回填历史，幸存者偏差仍在。
- 不生成订单、纸面目标或生产配置。
