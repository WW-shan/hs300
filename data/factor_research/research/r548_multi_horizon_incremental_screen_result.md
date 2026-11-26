# R548 多周期增量因子筛选

> 5 日筛选只作为 R547 的负知识；本轮检查与 60 日持有期更匹配的 20/60 日统计信号，仍不是组合收益结论。

## 基线

- stable7：`amount_level`, `bollinger_width`, `dividend_yield_quantile`, `fund_holding_ratio_absolute`, `max_return`, `turnover_level`, `vol_of_vol`
- 基线有效行：418,268
- 结论：`rejected`

## 结果

| 候选 | 周期 | 覆盖 | train IC | validation IC | recent IC | 增量 IC | 增量 p | q | stable7 相关 | 周期门槛 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `cf_r547_cash_quality` | 20d | 80.73% | 0.054693 | -0.012203 | 0.068804 | 0.005505 | 0.22618334 | 0.43584053 | 0.168533 | reject |
| `cf_r547_cash_quality` | 60d | 80.73% | 0.086215 | 0.012604 | 0.088655 | 0.006291 | 0.46163545 | 0.54363176 | 0.163538 | reject |
| `cf_r547_growth_quality` | 20d | 95.27% | -0.047373 | 0.075821 | 0.008567 | -0.018602 | 0.00766247 | 0.03575819 | 0.298683 | reject |
| `cf_r547_growth_quality` | 60d | 95.27% | -0.090428 | 0.108999 | 0.064393 | -0.03641 | 0.00112662 | 0.01577268 | 0.294391 | reject |
| `cf_r547_industry_quality` | 20d | 100.00% | -0.037899 | 0.002525 | 0.006353 | -0.016363 | 0.02231055 | 0.07808693 | 0.233927 | reject |
| `cf_r547_industry_quality` | 60d | 100.00% | -0.037819 | -0.020446 | 0.038038 | -0.018983 | 0.00532192 | 0.03575819 | 0.235427 | reject |
| `cf_r547_operating_efficiency` | 20d | 57.92% | 0.00883 | 0.032831 | 0.046268 | -0.005111 | 0.429114 | 0.54363176 | 0.063034 | reject |
| `cf_r547_operating_efficiency` | 60d | 57.92% | -0.004187 | 0.069801 | 0.097574 | -0.010113 | 0.2801832 | 0.43584053 | 0.061049 | reject |
| `cf_r547_smart_money_quality` | 20d | 94.54% | 0.013141 | -0.03054 | 0.015105 | -0.005287 | 0.26156704 | 0.43584053 | 0.168938 | reject |
| `cf_r547_smart_money_quality` | 60d | 94.54% | 0.011787 | -0.03103 | 0.049536 | -0.0107 | 0.16032657 | 0.43584053 | 0.17006 | reject |
| `cf_r547_value_quality` | 20d | 95.27% | 0.021522 | -0.016717 | 0.072653 | 0.001028 | 0.86278639 | 0.88583494 | 0.14197 | reject |
| `cf_r547_value_quality` | 60d | 95.27% | 0.034489 | -0.003486 | 0.09878 | 0.001144 | 0.88583494 | 0.88583494 | 0.143061 | reject |
| `cf_r547_value_quality_safety` | 20d | 95.27% | 0.038661 | -0.017113 | 0.063596 | 0.006528 | 0.27899551 | 0.43584053 | 0.120874 | reject |
| `cf_r547_value_quality_safety` | 60d | 95.27% | 0.061095 | 0.005584 | 0.077791 | 0.006648 | 0.46597008 | 0.54363176 | 0.120706 | reject |

## 入选与数据缺口

- finalists：无
- data gaps：['cf_r547_operating_efficiency']

## 边界

- This is a statistical incremental screen, not a portfolio backtest or trading recommendation.
- The 20/60-day forward returns overlap and use Newey-West lag equal to the horizon.
- Financial fields use project PIT announcement gating; extension sentiment/flow fields remain declared PIT.
- Recent-year values were observed in R547 and are diagnostic only; selection uses training and validation.
- A horizon can pass by multiple testing chance; the next formal add-one test must confirm it.
- The candidate definitions remain draft and no active version, orders or leverage are produced.
