# R536 全库 Finalist 独立簇与 FDR 复核

- R535 finalist：22
- 训练月末截面：32
- 相关性阈值：0.7
- 独立簇：13

## 代表因子

| 簇 | 代表 | 参数 | 训练 IC | validation IC | recent IC | q | 结论 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| amihud, amount_level | amount_level | 120 | 0.029257 | -0.002015 | 0.016783 | 0.88509755 | rejected |
| bollinger_width | bollinger_width | 10 | 0.022306 | 0.015443 | 0.014325 | 0.26088787 | rejected |
| dividend_yield_absolute, dividend_yield_quantile | dividend_yield_quantile | 0.7 | 0.0368 | 0.011372 | 0.027966 | 0.60952183 | rejected |
| fund_count_quantile | fund_count_quantile | 0.4 | 0.01649 | -0.013171 | -0.009681 | 0.14274306 | rejected |
| fund_holding_ratio_absolute, fund_holding_ratio_quantile | fund_holding_ratio_absolute | -0.05 | 0.027529 | -0.000804 | -0.000873 | 0.88509755 | rejected |
| lhb_inst_net_buy_event | lhb_inst_net_buy_event | 20 | 0.021472 | 0.008421 | 0.008335 | 0.75225389 | rejected |
| lhb_net_buy_event, lhb_net_buy_holding | lhb_net_buy_holding | 20 | 0.034937 | -0.0058 | -0.000258 | 0.85698184 | rejected |
| market_down_beta | market_down_beta | 120 | 0.018718 | -0.007792 | 0.011897 | 0.78284336 | rejected |
| annual_volatility, atr_risk, cs_spread, downside_volatility, idio_vol, max_return | max_return | 60 | 0.042375 | 0.027949 | 0.042663 | 0.11512969 | rejected |
| repurchase_event_decay | repurchase_event_decay | 20 | 0.030749 | 0.016813 | -0.001902 | 0.51659782 | rejected |
| repurchase_holding | repurchase_holding | 10 | 0.029953 | 0.004194 | 0.002144 | 0.87334826 | rejected |
| turnover_level | turnover_level | 120 | 0.023825 | 0.011047 | 0.024557 | 0.60952183 | rejected |
| vol_of_vol | vol_of_vol | 20 | 0.031098 | 0.017775 | 0.032002 | 0.19878075 | rejected |

## 结论

- 总体结论：rejected
- 准入家族：无

## 边界

- Clusters use training-period month-end factor ranks only.
- The 0.70 absolute-correlation threshold was fixed before validation outcomes.
- Representatives reuse R535A parameters and 5000-run training bootstrap evidence.
- This is a research factor selection, not a portfolio return or active strategy.
- R526 membership and industry vintages are reconstructed research inputs.
- R536 attempt 1 failed before publication because raw correlation NaN values reached the strict JSON ledger serializer; R536A maps non-finite values to null.
- No leverage, orders, paper targets or active version are produced.
