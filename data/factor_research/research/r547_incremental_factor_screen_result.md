# R547 增量因子统计筛选

> 这是项目数据上的统计筛选，不是组合收益或交易结论。finalist 只代表可以进入下一轮正式加一测试。

## 基线

- stable7：`amount_level`, `bollinger_width`, `dividend_yield_quantile`, `fund_holding_ratio_absolute`, `max_return`, `turnover_level`, `vol_of_vol`
- 基线覆盖行：418,268
- 最终结论：`rejected`

## 候选结果

| 候选 | 覆盖 | valid dates | train IC | validation IC | recent IC | 增量 IC | 增量 p | q | stable7 相关 | 筛选 | 近期诊断 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `cf_r547_cash_quality` | 80.73% | 1093 | 0.033819 | -0.010825 | 0.041497 | 0.002938 | 0.22489586 | 0.3148542 | 0.171614 | reject | positive |
| `cf_r547_growth_quality` | 95.27% | 1334 | -0.023759 | 0.04229 | -0.004585 | -0.008473 | 0.03380531 | 0.07887906 | 0.300662 | reject | negative |
| `cf_r547_industry_quality` | 100.00% | 1414 | -0.0219 | -0.016524 | 0.005223 | -0.009489 | 0.02350863 | 0.07887906 | 0.234471 | reject | positive |
| `cf_r547_operating_efficiency` | 57.92% | 850 | 0.004748 | 0.015712 | 0.028785 | -0.004208 | 0.16245157 | 0.28429025 | 0.063411 | reject | positive |
| `cf_r547_smart_money_quality` | 94.54% | 1327 | 0.00725 | -0.005876 | -0.004208 | -0.005144 | 0.02503106 | 0.07887906 | 0.166866 | reject | negative |
| `cf_r547_value_quality` | 95.27% | 1334 | 0.013597 | -0.009437 | 0.04171 | -4.7e-05 | 0.98878189 | 0.98878189 | 0.140825 | reject | positive |
| `cf_r547_value_quality_safety` | 95.27% | 1334 | 0.022548 | -0.013567 | 0.039895 | 0.003254 | 0.32575189 | 0.38004387 | 0.120955 | reject | positive |

## 入选与数据缺口

- finalists：无
- data gaps：cf_r547_operating_efficiency

## 边界

- Historical constituent and industry vintages are reconstructed project inputs, not provider publication snapshots.
- Financial fields use the project's statutory-deadline fallback and T+1 publication gate; this proves timing, not alpha.
- Sentiment/flow extension fields are declared PIT, not independently byte-audited in this round.
- Recent-year results were already visible in earlier research and are diagnostic only; finalist selection uses training and validation.
- A statistical incremental IC result is not a portfolio return, order instruction or production authorization.
- The research candidates remain draft definitions; no active version or leverage is produced.
