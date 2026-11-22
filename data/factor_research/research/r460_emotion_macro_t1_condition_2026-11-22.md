# R460 情绪/宏观 T+1 条件审计

| 条件变量 | 股票袖 | 20日高低差 | 20日q | 60日高低差 | 60日q |
| --- | --- | ---: | ---: | ---: | ---: |
| ext_alt_hs300_market_emotion_score | pb_latest_ocf_debt_debt_ratio_latest | 0.00785224 | None | -0.03005258 | None |
| ext_alt_hs300_market_emotion_score | ext_alt_hs300_idio_vol_40d_low | 0.0441076 | None | -0.04284603 | None |
| ext_alt_hs300_market_profit_score | pb_latest_ocf_debt_debt_ratio_latest | 0.00620369 | None | 0.06030979 | None |
| ext_alt_hs300_market_profit_score | ext_alt_hs300_idio_vol_40d_low | 0.00337935 | None | 0.0357019 | None |
| ext_alt_hs300_market_speculation_score | pb_latest_ocf_debt_debt_ratio_latest | -0.00074196 | None | 0.00721868 | None |
| ext_alt_hs300_market_speculation_score | ext_alt_hs300_idio_vol_40d_low | 0.02319878 | None | -0.05190065 | None |
| ext_alt_hs300_market_breadth_above_ma20 | pb_latest_ocf_debt_debt_ratio_latest | -0.00936417 | None | -0.04693364 | None |
| ext_alt_hs300_market_breadth_above_ma20 | ext_alt_hs300_idio_vol_40d_low | -0.04383713 | None | -0.10429388 | None |
| ext_alt_hs300_shibor_3m_chg_20d | pb_latest_ocf_debt_debt_ratio_latest | 0.08167826 | None | 0.11362316 | None |
| ext_alt_hs300_shibor_3m_chg_20d | ext_alt_hs300_idio_vol_40d_low | 0.10224527 | None | 0.06998561 | None |
| ext_alt_hs300_m1_m2_scissors_chg_1m | pb_latest_ocf_debt_debt_ratio_latest | -0.02024296 | None | 0.07897044 | None |
| ext_alt_hs300_m1_m2_scissors_chg_1m | ext_alt_hs300_idio_vol_40d_low | -0.01176929 | None | 0.03942214 | None |
| ext_alt_hs300_ah_premium_chg_1m | pb_latest_ocf_debt_debt_ratio_latest | 0.02265227 | None | 0.05618998 | None |
| ext_alt_hs300_ah_premium_chg_1m | ext_alt_hs300_idio_vol_40d_low | 0.01429115 | None | 0.08674977 | None |
| ext_alt_hs300_if_net_short_chg_1m | pb_latest_ocf_debt_debt_ratio_latest | -0.07903555 | None | 0.01829898 | None |
| ext_alt_hs300_if_net_short_chg_1m | ext_alt_hs300_idio_vol_40d_low | -0.04465858 | None | -0.01088431 | None |

- 通过单元：0

## 结论

把情绪和宏观状态改为月末计算、下一交易日生效后，没有任何条件单元同时通过效应量、配对月份 bootstrap、近期方向和多检验门禁。R458 的同月状态映射存在前视问题，相关结论只作历史负知识，R460 取代其数值。

## 边界

- Conditional IC is not a portfolio return.
- State labels are monthly and take effect only after month-end.
- Some macro conditioners are stale after 2026-10-16; the test end remains 2026-11-05 with as-of state carry only.
- Current HS300 membership and industry classification retain survivorship bias.
- No leverage and no production activation.
