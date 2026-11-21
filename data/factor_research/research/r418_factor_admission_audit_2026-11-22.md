# R418 因子库严格准入审计

| 因子 | 年度同号 | 近一年IC | 增量IC | q | 最大相关 | 准入 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| amihud_20d | None | None | 0.010909 | 0.03170599 | 0.015537453256525235 | 否 |
| ext_alt_hs300_dividend_yield | 1.0 | 0.045456 | 0.020831 | 0.03170599 | 0.04544568575720553 | 是 |
| ext_alt_hs300_fund_holding_ratio_low | 0.833333 | 0.00214 | 0.012083 | 0.03170599 | 0.05753556882996415 | 是 |
| ext_alt_hs300_holder_top10_share_chg_qoq | 0.333333 | 0.011456 | 0.007356 | 0.03170599 | 0.006725625803401323 | 否 |
| ext_alt_hs300_holder_top10_share_chg_yoy | 0.5 | 0.000907 | 0.007299 | 0.02337095 | 0.0034923163470146675 | 否 |
| ext_alt_hs300_idio_vol_40d_low | 1.0 | 0.035091 | 0.024488 | 0.00982908 | 0.167587009755977 | 是 |
| ext_alt_hs300_lhb_event_count_20d_low | 0.666667 | -0.006544 | 0.008958 | 0.02337095 | 0.062251384448360376 | 否 |
| ext_alt_hs300_lhb_inst_net_buy_ratio_20d | 0.666667 | -0.009586 | 0.003449 | 0.07808412 | 0.016700939325261947 | 否 |
| ext_alt_hs300_short_ratio_low | 0.666667 | -0.006648 | 0.013379 | 0.03170599 | 0.06645470468156055 | 否 |
| ext_alt_hs300_vol_of_vol_60d_low | 0.833333 | 0.044244 | 0.015251 | 0.07889682 | 0.061439766193789935 | 是 |
| inventory_turnover_improvement_yoy | None | None | 0.009886 | 0.01960876 | 0.06062604037809711 | 否 |

- 准入因子数：4
- 准入名单：ext_alt_hs300_dividend_yield, ext_alt_hs300_fund_holding_ratio_low, ext_alt_hs300_idio_vol_40d_low, ext_alt_hs300_vol_of_vol_60d_low

## 拒绝原因

- amihud_20d: yearly_sign_consistency, recent_ic_positive
- ext_alt_hs300_holder_top10_share_chg_qoq: yearly_sign_consistency
- ext_alt_hs300_holder_top10_share_chg_yoy: yearly_sign_consistency
- ext_alt_hs300_lhb_event_count_20d_low: yearly_sign_consistency, recent_ic_positive
- ext_alt_hs300_lhb_inst_net_buy_ratio_20d: yearly_sign_consistency, recent_ic_positive
- ext_alt_hs300_short_ratio_low: yearly_sign_consistency, recent_ic_positive
- inventory_turnover_improvement_yoy: yearly_sign_consistency, recent_ic_positive

## 边界

- R391 is a 5-day statistical screen, not a portfolio alpha proof.
- Factors lacking yearly/recent metrics fail closed.
- The test window has been observed before.
- No leverage and no portfolio promotion.
