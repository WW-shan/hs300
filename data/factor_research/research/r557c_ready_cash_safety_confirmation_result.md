# R557 可直接回测的 cash_safety 正式确认

> `project_formal`：标准策略 + 自定义因子 + 内置仓位策略 + 通用行业上限。

## 配置

- 策略：`ai_hs300_cash_safety_v14`
- 因子：`uf_r550_cash_safety_rank`
- 仓位策略：`up_vol_target_14`
- 参数：{'max_positions': 10, 'max_hold_days': 60, 'stop_loss': -0.12, 'entry_score': 70.0, 'exit_score': 40.0, 'top_rank': 20, 'max_industry_positions': 3}

## 结果

| 口径 | 全周期年化 | Sharpe | MDD |
| --- | ---: | ---: | ---: |
| 可直接回测策略 | 13.95% | 1.10 | -12.89% |
| R552C 参考 | 11.10% | 0.92 | -14.43% |

## 门槛

- full_annual_min: 通过
- full_sharpe_min: 通过
- full_mdd_min: 通过
- validation_annual_positive: 未过
- stress_3x_annual_min: 通过
- recent_return_positive: 通过
- fresh_positive_min: 通过
- fresh_median_positive: 通过
- long_phase_positive_min: 通过
- long_phase_median_positive: 通过
- reference_annual_delta_min: 通过
- reference_sharpe_delta_min: 通过

## 边界

- The ready strategy uses a registered rank composite whose standalone validation IC was negative in R556B.
- Formal runs inject the research industry map; the UI fallback can use the current local industry map and therefore differ slightly.
- Recent windows have already been observed and are not independent out-of-sample data.
- GBM is a risk-mechanics null and cannot prove real financial alpha.
- No active version, orders, paper targets or production enablement are produced.
