# R416 情绪交互因子统计筛查

| 因子 | 覆盖 | 边际IC | NW p | q | 相关 | 近一年增量 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| new_panic_reversal_5d | 42.77% | 0.001183 | 0.02572558 | 0.0772 | 0.215856 | 0.001157 | statistical_pass |
| new_repair_momentum_10d | 23.36% | -0.000991 | 0.21086139 | 0.3163 | 0.241139 | 0.000213 | statistical_rejected |
| new_crowding_reversal_20d | 11.86% | -0.000168 | 0.87502306 | 0.8750 | 0.317744 | -0.002569 | statistical_rejected |

## 门槛

- new_panic_reversal_5d: marginal_ic_positive=通过, nw_p_max=通过, bh_q_max=通过, max_correlation_max=通过, minimum_n_dates=通过, recent_delta_positive=通过
- new_repair_momentum_10d: marginal_ic_positive=未过, nw_p_max=未过, bh_q_max=未过, max_correlation_max=通过, minimum_n_dates=通过, recent_delta_positive=通过
- new_crowding_reversal_20d: marginal_ic_positive=未过, nw_p_max=未过, bh_q_max=未过, max_correlation_max=通过, minimum_n_dates=通过, recent_delta_positive=未过

## 边界

- New formulas are research-only and not registered factors until independently confirmed.
- Regime labels are lagged but not a full real-time vintage audit.
- Current HS300 membership applied to history retains survivorship bias.
- The evaluation window has been observed before.
- No leverage and no portfolio promotion in this screen.
