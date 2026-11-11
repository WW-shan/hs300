# 因子参数族分类报告

- 报告 ID：`sha256:044a054b71571ea0128d2b6929562874333b2b0c74c878c61d006a313a5acba9`
- 生成时间：`2026-11-11T11:09:35Z`
- Catalog：`sha256:8c5e3b58937d3399313c8903353a4df01fcb4ecf30c29837633cba00f2006543`
- 注册因子：223
- 参数族：62

## 分类汇总

| 状态 | 数量 |
| --- | ---: |
| tunable | 0 |
| fixed | 121 |
| grouped | 82 |
| blocked | 20 |
| unclassified | 0 |

`unclassified` 为 fail-closed 结果：这些因子出现数字窗口迹象，但尚未登记参数族，不能进入调参或发布。

## 参数族

| 参数族 | 参数网格 | 当前参数 | 成员数 | 状态 |
| --- | --- | ---: | ---: | --- |
| `amihud` | 5, 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `amount_level` | 5, 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `amount_spike` | 5, 10, 20, 60 | 5 | 1 | tunable |
| `analyst_rating_change` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `analyst_rating_level` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `annual_volatility` | 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `atr_risk` | 5, 10, 14, 20, 60 | 14 | 1 | tunable |
| `beta_asymmetry` | 60, 120, 180, 240 | 120 | 1 | tunable |
| `block_premium_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `block_trade_ratio_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `bollinger_position` | 10, 20, 40, 60 | 20 | 1 | tunable |
| `bollinger_width` | 10, 20, 40, 60 | 20 | 1 | tunable |
| `bond_futures_momentum` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `bond_futures_trend_momentum` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `cs_spread` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `distance_from_low` | 20, 60, 120, 240 | 60 | 1 | tunable |
| `distance_to_high` | 20, 60, 120, 240 | 240 | 2 | tunable |
| `downside_volatility` | 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `etf_share_change` | 1, 2, 3, 6 | 1 | 2 | tunable |
| `hscei_momentum` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `hsi_momentum` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `hsi_relative_hs300` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `idio_vol` | 20, 40, 60, 120 | 60 | 3 | tunable |
| `industry_breadth` | 10, 20, 40, 60 | 20 | 1 | tunable |
| `industry_momentum` | 10, 20, 40, 60, 120 | 20 | 2 | tunable |
| `industry_rps` | 10, 20, 40, 60 | 20 | 1 | tunable |
| `insider_buy_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `insider_net_buy_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `insider_sell_event_low` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `intraday_return` | 5, 10, 20, 60 | 20 | 1 | tunable |
| `lhb_event_count_low` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `lhb_inst_net_buy_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `lhb_net_buy_event` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `limit_up_gene` | 5, 10, 20, 40, 60, 120 | 20 | 2 | tunable |
| `market_down_beta` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `market_skew` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `max_return` | 5, 10, 20, 60 | 20 | 1 | tunable |
| `medium_ema_trend` | 20, 30, 60, 120 | 20 | 3 | tunable |
| `medium_ma_trend` | 20, 30, 60, 120 | 20 | 3 | tunable |
| `medium_momentum` | 20, 30, 60, 120 | 20 | 4 | tunable |
| `obv_trend` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `overnight_return` | 5, 10, 20, 60 | 20 | 1 | tunable |
| `price_delay` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `price_position` | 60, 120, 240 | 240 | 1 | tunable |
| `qvix_zscore` | 60, 120, 252 | 252 | 1 | tunable |
| `repurchase_event_decay` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `return_skew` | 5, 10, 20, 60 | 20 | 1 | tunable |
| `risk_adjusted_momentum` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `rsi_reversal` | 6, 14, 24, 40, 60 | 14 | 3 | tunable |
| `short_ema_reversal` | 3, 5, 10, 15, 20 | 5 | 2 | tunable |
| `short_ma_reversal` | 3, 5, 10, 15, 20 | 5 | 2 | tunable |
| `short_momentum_reversal` | 3, 5, 10, 15, 20 | 5 | 2 | tunable |
| `stock_rel_industry_strength` | 10, 20, 40, 60 | 20 | 1 | tunable |
| `trend_efficiency` | 20, 40, 60, 120 | 60 | 1 | tunable |
| `turnover_crowding` | 10, 20, 60, 120, 240 | 60 | 1 | tunable |
| `turnover_level` | 5, 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `turnover_spike` | 5, 10, 20, 60 | 5 | 1 | tunable |
| `turnover_stability` | 5, 10, 20, 40, 60, 120 | 20 | 1 | tunable |
| `up_day_ratio` | 5, 10, 20, 60 | 20 | 1 | tunable |
| `vol_of_vol` | 20, 40, 60, 120 | 60 | 2 | tunable |
| `volume_price_corr` | 5, 10, 20, 40, 60 | 20 | 1 | tunable |
| `volume_spike` | 5, 10, 20, 60 | 5 | 2 | tunable |

## 因子状态

| 因子 | 状态 | 参数族 | 识别参数 | 原因 |
| --- | --- | --- | ---: | --- |
| `accrual` | fixed | - | - | no tunable numeric window detected and no family membership |
| `amihud_20d` | grouped | `amihud` | 20 | member of parameter family amihud |
| `amount_mean_20d` | grouped | `amount_level` | 20 | member of parameter family amount_level |
| `amount_ratio_5d` | grouped | `amount_spike` | 5 | member of parameter family amount_spike |
| `amplitude` | fixed | - | - | no tunable numeric window detected and no family membership |
| `amplitude_trend_20_60` | fixed | - | 20 | explicitly fixed by parameter registry |
| `annual_vol_20d` | grouped | `annual_volatility` | 20 | member of parameter family annual_volatility |
| `atr_14` | blocked | - | 14 | explicitly blocked by parameter registry |
| `atr_pct` | grouped | `atr_risk` | - | member of parameter family atr_risk |
| `boll_position` | grouped | `bollinger_position` | - | member of parameter family bollinger_position |
| `boll_width` | grouped | `bollinger_width` | - | member of parameter family bollinger_width |
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
| `distance_from_low_60d` | grouped | `distance_from_low` | 60 | member of parameter family distance_from_low |
| `distance_to_high_240d` | grouped | `distance_to_high` | 240 | member of parameter family distance_to_high |
| `distance_to_high_60d` | grouped | `distance_to_high` | 60 | member of parameter family distance_to_high |
| `downside_vol_20d` | grouped | `downside_volatility` | 20 | member of parameter family downside_volatility |
| `ema10_bias` | grouped | `short_ema_reversal` | 10 | member of parameter family short_ema_reversal |
| `ema20_bias` | grouped | `medium_ema_trend` | 20 | member of parameter family medium_ema_trend |
| `ema30_bias` | grouped | `medium_ema_trend` | 30 | member of parameter family medium_ema_trend |
| `ema5_bias` | grouped | `short_ema_reversal` | 5 | member of parameter family short_ema_reversal |
| `ema60_bias` | grouped | `medium_ema_trend` | 60 | member of parameter family medium_ema_trend |
| `ext_alt_hs300_ah_premium_chg_1m` | fixed | - | 1 | fixed market conditioner with one audited monthly horizon |
| `ext_alt_hs300_ah_premium_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_analyst_rating_change_20d` | grouped | `analyst_rating_change` | 20 | member of parameter family analyst_rating_change |
| `ext_alt_hs300_analyst_rating_score_20d` | grouped | `analyst_rating_level` | 20 | member of parameter family analyst_rating_level |
| `ext_alt_hs300_beta_asymmetry_120d` | grouped | `beta_asymmetry` | 120 | member of parameter family beta_asymmetry |
| `ext_alt_hs300_block_premium_20d` | grouped | `block_premium_event` | 20 | member of parameter family block_premium_event |
| `ext_alt_hs300_block_trade_ratio_20d_low` | grouped | `block_trade_ratio_event` | 20 | member of parameter family block_trade_ratio_event |
| `ext_alt_hs300_bond_mom_20d` | grouped | `bond_futures_momentum` | 20 | member of parameter family bond_futures_momentum |
| `ext_alt_hs300_bond_tf_mom_20d` | grouped | `bond_futures_trend_momentum` | 20 | member of parameter family bond_futures_trend_momentum |
| `ext_alt_hs300_capex_intensity_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_cash_flow_stability_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
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
| `ext_alt_hs300_equity_supply_ratio_180d_low` | blocked | - | 180 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_etf_share_chg_1m` | grouped | `etf_share_change` | 1 | member of parameter family etf_share_change |
| `ext_alt_hs300_etf_share_chg_3m` | grouped | `etf_share_change` | 3 | member of parameter family etf_share_change |
| `ext_alt_hs300_express_surprise_60d` | blocked | - | 60 | future_data_status=unknown; announcement-date source requires catalog audit promotion |
| `ext_alt_hs300_forecast_surprise_60d` | blocked | - | 60 | future_data_status=unknown; announcement-date source requires catalog audit promotion |
| `ext_alt_hs300_fund_count_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_count_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_ratio_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_fund_holding_value_chg_qoq_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_goodwill_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_gross_margin_stability_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_gross_margin_yoy_improvement_q` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_net_buy_count_90d` | fixed | - | 90 | fixed structural 90-day lookback; threshold work belongs to P4 |
| `ext_alt_hs300_holder_net_buy_ratio_90d` | fixed | - | 90 | fixed structural 90-day lookback; threshold work belongs to P4 |
| `ext_alt_hs300_holder_top10_free_float_share` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_top10_share_chg_qoq` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_holder_top10_share_chg_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_stock_rel_industry_top3_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_chg_20d` | fixed | - | 20 | fixed market conditioner with one audited turnover horizon |
| `ext_alt_hs300_hs300_turnover_chg_20d_ext2017` | fixed | - | 20 | fixed duplicate extended-history turnover variant; no independent parameter family |
| `ext_alt_hs300_hs300_turnover_gate_flag_ext2017` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hs300_turnover_z_ext2017` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_hsciei_mom_20d` | grouped | `hscei_momentum` | 20 | member of parameter family hscei_momentum |
| `ext_alt_hs300_hsi_mom_20d` | grouped | `hsi_momentum` | 20 | member of parameter family hsi_momentum |
| `ext_alt_hs300_hsi_rel_hs300_20d` | grouped | `hsi_relative_hs300` | 20 | member of parameter family hsi_relative_hs300 |
| `ext_alt_hs300_idio_vol_20d_low` | grouped | `idio_vol` | 20 | member of parameter family idio_vol |
| `ext_alt_hs300_idio_vol_40d_low` | grouped | `idio_vol` | 40 | member of parameter family idio_vol |
| `ext_alt_hs300_idio_vol_60d_low` | grouped | `idio_vol` | 60 | member of parameter family idio_vol |
| `ext_alt_hs300_if_net_short_chg_1m` | fixed | - | 1 | fixed market conditioner with one audited monthly horizon |
| `ext_alt_hs300_industry_breadth_20d` | grouped | `industry_breadth` | 20 | member of parameter family industry_breadth |
| `ext_alt_hs300_industry_mom_20d` | grouped | `industry_momentum` | 20 | member of parameter family industry_momentum |
| `ext_alt_hs300_industry_mom_60d` | grouped | `industry_momentum` | 60 | member of parameter family industry_momentum |
| `ext_alt_hs300_industry_rps_20d` | grouped | `industry_rps` | 20 | member of parameter family industry_rps |
| `ext_alt_hs300_insider_buy_20d` | grouped | `insider_buy_event` | 20 | member of parameter family insider_buy_event |
| `ext_alt_hs300_insider_net_buy_20d` | grouped | `insider_net_buy_event` | 20 | member of parameter family insider_net_buy_event |
| `ext_alt_hs300_insider_sell_20d_low` | grouped | `insider_sell_event_low` | 20 | member of parameter family insider_sell_event_low |
| `ext_alt_hs300_intangible_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_lhb_event_count_20d_low` | grouped | `lhb_event_count_low` | 20 | member of parameter family lhb_event_count_low |
| `ext_alt_hs300_lhb_inst_net_buy_ratio_20d` | grouped | `lhb_inst_net_buy_event` | 20 | member of parameter family lhb_inst_net_buy_event |
| `ext_alt_hs300_lhb_net_buy_ratio_20d` | grouped | `lhb_net_buy_event` | 20 | member of parameter family lhb_net_buy_event |
| `ext_alt_hs300_m1_m2_scissors` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_m1_m2_scissors_chg_1m` | fixed | - | 1 | fixed macro conditioner with one audited monthly horizon |
| `ext_alt_hs300_m2_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_leader_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_strength` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_mainline_top1_flag` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_management_expense_ratio_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_margin_chg_1m_low` | fixed | - | 1 | fixed structural monthly change; threshold work belongs to P4 |
| `ext_alt_hs300_margin_safety_chg_20d` | fixed | - | 20 | fixed market conditioner with one audited monthly horizon |
| `ext_alt_hs300_margin_safety_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_breadth_above_ma20` | fixed | - | 20 | fixed regime field with one audited breadth definition |
| `ext_alt_hs300_market_down_beta_60d_low` | grouped | `market_down_beta` | 60 | member of parameter family market_down_beta |
| `ext_alt_hs300_market_emotion_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_profit_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_skew_60d_low` | grouped | `market_skew` | 60 | member of parameter family market_skew |
| `ext_alt_hs300_market_speculation_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_market_trend_score` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_net_financing_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_nh_nl_diff_20d` | fixed | - | 20 | fixed market conditioner with one audited monthly horizon |
| `ext_alt_hs300_nh_nl_diff_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_northbound_net_buy_20d` | blocked | - | 20 | stale: last non-null 2024-08-16 |
| `ext_alt_hs300_ocf_margin_4q` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_pledge_ratio_chg_1m_low` | fixed | - | 1 | fixed structural monthly change; threshold work belongs to P4 |
| `ext_alt_hs300_pledge_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_pmi` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_ppi_yoy` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_price_delay_60d` | grouped | `price_delay` | 60 | member of parameter family price_delay |
| `ext_alt_hs300_qvix_z_252` | grouped | `qvix_zscore` | 252 | member of parameter family qvix_zscore |
| `ext_alt_hs300_rd_intensity` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_repurchase_event_60d` | grouped | `repurchase_event_decay` | 60 | member of parameter family repurchase_event_decay |
| `ext_alt_hs300_risk_adjusted_momentum_60d` | grouped | `risk_adjusted_momentum` | 60 | member of parameter family risk_adjusted_momentum |
| `ext_alt_hs300_sga_ratio_4q_industry_neutral` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_sga_ratio_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_sga_ratio_4q_qoq_improvement` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_sga_ratio_stability_4q_low` | blocked | - | 4 | future_data_status=unknown; field-level PIT promotion required |
| `ext_alt_hs300_shibor_3m` | fixed | - | 3 | fixed macro level |
| `ext_alt_hs300_shibor_3m_chg_20d` | fixed | - | 20 | fixed macro conditioner with one audited horizon |
| `ext_alt_hs300_shibor_tightness_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_short_ratio_low` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_stock_rel_industry_20d` | grouped | `stock_rel_industry_strength` | 20 | member of parameter family stock_rel_industry_strength |
| `ext_alt_hs300_term_spread_10y_2y` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_top30_amount_share_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_trend_efficiency_60d` | grouped | `trend_efficiency` | 60 | member of parameter family trend_efficiency |
| `ext_alt_hs300_turnover_hhi_z` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ext_alt_hs300_vol_of_vol_20d_low` | grouped | `vol_of_vol` | 20 | member of parameter family vol_of_vol |
| `ext_alt_hs300_vol_of_vol_60d_low` | grouped | `vol_of_vol` | 60 | member of parameter family vol_of_vol |
| `gap_return` | fixed | - | - | no tunable numeric window detected and no family membership |
| `gross_margin_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `intraday_ret_20d` | grouped | `intraday_return` | 20 | member of parameter family intraday_return |
| `intraday_return` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_d` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_j` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_k` | fixed | - | - | no tunable numeric window detected and no family membership |
| `kdj_kd_diff` | fixed | - | - | no tunable numeric window detected and no family membership |
| `limit_up_count_20d` | grouped | `limit_up_gene` | 20 | member of parameter family limit_up_gene |
| `limit_up_count_60d` | grouped | `limit_up_gene` | 60 | member of parameter family limit_up_gene |
| `log_amount` | fixed | - | - | no tunable numeric window detected and no family membership |
| `log_float_mv` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ma10_bias` | grouped | `short_ma_reversal` | 10 | member of parameter family short_ma_reversal |
| `ma20_bias` | grouped | `medium_ma_trend` | 20 | member of parameter family medium_ma_trend |
| `ma30_bias` | grouped | `medium_ma_trend` | 30 | member of parameter family medium_ma_trend |
| `ma5_bias` | grouped | `short_ma_reversal` | 5 | member of parameter family short_ma_reversal |
| `ma60_bias` | grouped | `medium_ma_trend` | 60 | member of parameter family medium_ma_trend |
| `macd_dea_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_dif_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_hist` | fixed | - | - | no tunable numeric window detected and no family membership |
| `macd_hist_pct` | fixed | - | - | no tunable numeric window detected and no family membership |
| `max_ret_20d` | grouped | `max_return` | 20 | member of parameter family max_return |
| `mom_accel_20_60` | fixed | - | 20 | explicitly fixed by parameter registry |
| `momentum_10d` | grouped | `short_momentum_reversal` | 10 | member of parameter family short_momentum_reversal |
| `momentum_120d` | grouped | `medium_momentum` | 120 | member of parameter family medium_momentum |
| `momentum_20d` | grouped | `medium_momentum` | 20 | member of parameter family medium_momentum |
| `momentum_30d` | grouped | `medium_momentum` | 30 | member of parameter family medium_momentum |
| `momentum_5d` | grouped | `short_momentum_reversal` | 5 | member of parameter family short_momentum_reversal |
| `momentum_60d` | grouped | `medium_momentum` | 60 | member of parameter family medium_momentum |
| `net_income_yoy_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `net_margin_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `obv_trend_20d` | grouped | `obv_trend` | 20 | member of parameter family obv_trend |
| `ocf_debt` | fixed | - | - | no tunable numeric window detected and no family membership |
| `ocf_ni` | fixed | - | - | no tunable numeric window detected and no family membership |
| `overnight_ret_20d` | grouped | `overnight_return` | 20 | member of parameter family overnight_return |
| `pb_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `position_240d` | grouped | `price_position` | 240 | member of parameter family price_position |
| `ret_skew_20d` | grouped | `return_skew` | 20 | member of parameter family return_skew |
| `revenue_yoy_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `roe_latest` | fixed | - | - | no tunable numeric window detected and no family membership |
| `rsi_14` | grouped | `rsi_reversal` | 14 | member of parameter family rsi_reversal |
| `rsi_14_delta_5d` | fixed | - | 5 | explicitly fixed by parameter registry |
| `rsi_24` | grouped | `rsi_reversal` | 24 | member of parameter family rsi_reversal |
| `rsi_6` | grouped | `rsi_reversal` | 6 | member of parameter family rsi_reversal |
| `turnover_mean_20d` | grouped | `turnover_level` | 20 | member of parameter family turnover_level |
| `turnover_rate` | fixed | - | - | no tunable numeric window detected and no family membership |
| `turnover_ratio_5d` | grouped | `turnover_spike` | 5 | member of parameter family turnover_spike |
| `turnover_std_20d` | grouped | `turnover_stability` | 20 | member of parameter family turnover_stability |
| `turnover_z_60d` | grouped | `turnover_crowding` | 60 | member of parameter family turnover_crowding |
| `uf_cash_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_close_position_reversal` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_core_earnings_quality` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_ep_roe_pb` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_factor_agreement` | fixed | - | - | no tunable numeric window detected and no family membership |
| `uf_industry_relative_strength_change_5d` | blocked | - | 5 | custom definition pit_status=unknown and future_data_status=unknown; source file is uncommitted user data |
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
| `up_days_20d` | grouped | `up_day_ratio` | 20 | member of parameter family up_day_ratio |
| `vol_price_corr_20d` | grouped | `volume_price_corr` | 20 | member of parameter family volume_price_corr |
| `vol_ratio_10d` | grouped | `volume_spike` | 10 | member of parameter family volume_spike |
| `vol_ratio_5d` | grouped | `volume_spike` | 5 | member of parameter family volume_spike |
| `vol_regime_5_60` | fixed | - | 5 | explicitly fixed by parameter registry |
| `vol_trend_5_10` | fixed | - | 5 | explicitly fixed by parameter registry |
| `vol_trend_5_60` | fixed | - | 5 | explicitly fixed by parameter registry |
| `vwap_bias` | fixed | - | - | no tunable numeric window detected and no family membership |
