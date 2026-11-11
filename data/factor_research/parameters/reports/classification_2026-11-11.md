# 因子参数族分类报告

- 报告 ID：`sha256:ff218b89c7e831a74e896640d73fd05adcc314779f8f449b9ded98f124ba7b7f`
- 生成时间：`2026-11-11T08:43:19Z`
- Catalog：`sha256:8c5e3b58937d3399313c8903353a4df01fcb4ecf30c29837633cba00f2006543`
- 注册因子：223
- 参数族：8

## 分类汇总

| 状态 | 数量 |
| --- | ---: |
| tunable | 0 |
| fixed | 104 |
| grouped | 11 |
| blocked | 5 |
| unclassified | 103 |

`unclassified` 为 fail-closed 结果：这些因子出现数字窗口迹象，但尚未登记参数族，不能进入调参或发布。

## 参数族

| 参数族 | 参数网格 | 当前参数 | 成员数 | 状态 |
| --- | --- | ---: | ---: | --- |
| `beta_asymmetry` | 60, 120, 180, 240 | 120 | 1 | tunable |
| `cs_spread` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `idio_vol` | 20, 40, 60, 120 | 60 | 3 | tunable |
| `market_down_beta` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `price_delay` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `risk_adjusted_momentum` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `trend_efficiency` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `vol_of_vol` | 20, 40, 60, 120 | 60 | 2 | tunable |

## 因子状态

| 因子 | 状态 | 参数族 | 识别参数 | 原因 |
| --- | --- | --- | ---: | --- |
| `accrual` | fixed | - | - | no tunable numeric window detected and no family membership |
| `amihud_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `amount_mean_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `amount_ratio_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `amplitude` | fixed | - | - | no tunable numeric window detected and no family membership |
| `amplitude_trend_20_60` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `annual_vol_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `atr_14` | unclassified | - | 14 | numeric parameter window is not registered in a parameter family |
| `atr_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `boll_position` | fixed | - | - | no tunable numeric window detected and no family membership |
| `boll_width` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_fundamental_growth_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_fundamental_safety_cash` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_industry_cash_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_industry_fund_reduction` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_industry_lowrisk_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_liquidity_structure` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_medium_term_trend` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_price_position` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_reversal_sentiment` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_short_term_risk_reversal` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_tail_risk_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_trend_confirmation` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_volume_price_structure` | fixed | - | - | no tunable numeric window detected and no family membership |
| `cf_weak_reversal_blend` | fixed | - | - | no tunable numeric window detected and no family membership |
| `change_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `close_position` | fixed | - | - | no tunable numeric window detected and no family membership |
| `core_earnings_ratio` | fixed | - | - | no tunable numeric window detected and no family membership |
| `debt_ratio_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `distance_from_low_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `distance_to_high_240d` | unclassified | - | 240 | numeric parameter window is not registered in a parameter family |
| `distance_to_high_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `downside_vol_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ema10_bias` | unclassified | - | 10 | numeric parameter window is not registered in a parameter family |
| `ema20_bias` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ema30_bias` | unclassified | - | 30 | numeric parameter window is not registered in a parameter family |
| `ema5_bias` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `ema60_bias` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_ah_premium_chg_1m` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_ah_premium_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_analyst_rating_change_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_analyst_rating_score_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_beta_asymmetry_120d` | grouped | `beta_asymmetry` | 120 | member of parameter family beta_asymmetry |
| `ext_alt_hs300_block_premium_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_block_trade_ratio_20d_low` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_bond_mom_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_bond_tf_mom_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_capex_intensity_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_cash_flow_stability_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_cash_to_assets` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_cpi_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_credit_spread_5y_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_cs_spread_60d` | grouped | `cs_spread` | 60 | member of parameter family cs_spread |
| `ext_alt_hs300_dividend_growth_ttm` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_dividend_initiation_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_dividend_ttm_cash` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_dividend_yield` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_equity_risk_premium` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_equity_risk_premium_chg3m` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_equity_supply_ratio_180d_low` | unclassified | - | 180 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_etf_share_chg_1m` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_etf_share_chg_3m` | unclassified | - | 3 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_express_surprise_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_forecast_surprise_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_fund_count_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_count_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_ratio_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_value_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_goodwill_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_gross_margin_stability_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_gross_margin_yoy_improvement_q` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_net_buy_count_90d` | unclassified | - | 90 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_holder_net_buy_ratio_90d` | unclassified | - | 90 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_holder_top10_free_float_share` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_top10_share_chg_qoq` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_top10_share_chg_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_stock_rel_industry_top3_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_chg_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_hs300_turnover_chg_20d_ext2017` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_hs300_turnover_gate_flag_ext2017` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_z_ext2017` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hsciei_mom_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_hsi_mom_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_hsi_rel_hs300_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_idio_vol_20d_low` | grouped | `idio_vol` | 20 | member of parameter family idio_vol |
| `ext_alt_hs300_idio_vol_40d_low` | grouped | `idio_vol` | 40 | member of parameter family idio_vol |
| `ext_alt_hs300_idio_vol_60d_low` | grouped | `idio_vol` | 60 | member of parameter family idio_vol |
| `ext_alt_hs300_if_net_short_chg_1m` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_industry_breadth_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_industry_mom_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_industry_mom_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_industry_rps_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_insider_buy_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_insider_net_buy_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_insider_sell_20d_low` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_intangible_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_lhb_event_count_20d_low` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_lhb_inst_net_buy_ratio_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_lhb_net_buy_ratio_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_m1_m2_scissors` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_m1_m2_scissors_chg_1m` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_m2_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_leader_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_strength` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_top1_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_management_expense_ratio_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_margin_chg_1m_low` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_margin_safety_chg_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_margin_safety_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_breadth_above_ma20` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_market_down_beta_60d_low` | grouped | `market_down_beta` | 60 | member of parameter family market_down_beta |
| `ext_alt_hs300_market_emotion_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_profit_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_skew_60d_low` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_market_speculation_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_trend_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_net_financing_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_nh_nl_diff_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_nh_nl_diff_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_northbound_net_buy_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_ocf_margin_4q` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_pledge_ratio_chg_1m_low` | unclassified | - | 1 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_pledge_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_pmi` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_ppi_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_price_delay_60d` | grouped | `price_delay` | 60 | member of parameter family price_delay |
| `ext_alt_hs300_qvix_z_252` | unclassified | - | 252 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_rd_intensity` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_repurchase_event_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_risk_adjusted_momentum_60d` | grouped | `risk_adjusted_momentum` | 60 | member of parameter family risk_adjusted_momentum |
| `ext_alt_hs300_sga_ratio_4q_industry_neutral` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_sga_ratio_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_sga_ratio_4q_qoq_improvement` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_sga_ratio_stability_4q_low` | unclassified | - | 4 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_shibor_3m` | unclassified | - | 3 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_shibor_3m_chg_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_shibor_tightness_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_short_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_stock_rel_industry_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ext_alt_hs300_term_spread_10y_2y` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_top30_amount_share_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_trend_efficiency_60d` | grouped | `trend_efficiency` | 60 | member of parameter family trend_efficiency |
| `ext_alt_hs300_turnover_hhi_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_vol_of_vol_20d_low` | grouped | `vol_of_vol` | 20 | member of parameter family vol_of_vol |
| `ext_alt_hs300_vol_of_vol_60d_low` | grouped | `vol_of_vol` | 60 | member of parameter family vol_of_vol |
| `gap_return` | fixed | - | - | no tunable numeric window detected and no family membership |
| `gross_margin_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `intraday_ret_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `intraday_return` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_d` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_j` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_k` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_kd_diff` | fixed | - | - | no tunable numeric window detected and no family membership |
| `limit_up_count_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `limit_up_count_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `log_amount` | fixed | - | - | no tunable numeric window detected and no family membership |
| `log_float_mv` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ma10_bias` | unclassified | - | 10 | numeric parameter window is not registered in a parameter family |
| `ma20_bias` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ma30_bias` | unclassified | - | 30 | numeric parameter window is not registered in a parameter family |
| `ma5_bias` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `ma60_bias` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `macd_dea_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_dif_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_hist` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_hist_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `max_ret_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `mom_accel_20_60` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `momentum_10d` | unclassified | - | 10 | numeric parameter window is not registered in a parameter family |
| `momentum_120d` | unclassified | - | 120 | numeric parameter window is not registered in a parameter family |
| `momentum_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `momentum_30d` | unclassified | - | 30 | numeric parameter window is not registered in a parameter family |
| `momentum_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `momentum_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `net_income_yoy_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `net_margin_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `obv_trend_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `ocf_debt` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ocf_ni` | fixed | - | - | no tunable numeric window detected and no family membership |
| `overnight_ret_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `pb_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `position_240d` | unclassified | - | 240 | numeric parameter window is not registered in a parameter family |
| `ret_skew_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `revenue_yoy_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `roe_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `rsi_14` | unclassified | - | 14 | numeric parameter window is not registered in a parameter family |
| `rsi_14_delta_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `rsi_24` | unclassified | - | 24 | numeric parameter window is not registered in a parameter family |
| `rsi_6` | unclassified | - | 6 | numeric parameter window is not registered in a parameter family |
| `turnover_mean_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `turnover_rate` | fixed | - | - | no tunable numeric window detected and no family membership |
| `turnover_ratio_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `turnover_std_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `turnover_z_60d` | unclassified | - | 60 | numeric parameter window is not registered in a parameter family |
| `uf_cash_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_close_position_reversal` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_core_earnings_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_ep_roe_pb` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_factor_agreement` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_industry_relative_strength_change_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `uf_intraday_reversal` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_intraday_risk_share` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_low_amount` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_low_amount_20` | blocked | - | 20 | factor definition or future-data status is blocked |
| `uf_low_turnover` | blocked | - | - | factor definition or future-data status is blocked |
| `uf_low_vol` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_low_vol_60` | blocked | - | 60 | factor definition or future-data status is blocked |
| `uf_merton_dd` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_quality_value_defensive` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_rev_10d` | blocked | - | 10 | factor definition or future-data status is blocked |
| `uf_rev_5d` | blocked | - | 5 | factor definition or future-data status is blocked |
| `uf_small_mv` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_solvency_buffer` | fixed | - | - | no tunable numeric window detected and no family membership |
| `up_days_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `vol_price_corr_20d` | unclassified | - | 20 | numeric parameter window is not registered in a parameter family |
| `vol_ratio_10d` | unclassified | - | 10 | numeric parameter window is not registered in a parameter family |
| `vol_ratio_5d` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `vol_regime_5_60` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `vol_trend_5_10` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `vol_trend_5_60` | unclassified | - | 5 | numeric parameter window is not registered in a parameter family |
| `vwap_bias` | fixed | - | - | no tunable numeric window detected and no family membership |
