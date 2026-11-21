# R439 双准入复核

| 因子 | P6就绪 | R418准入 | 双准入 | 原因 |
| --- | :---: | :---: | :---: | --- |
| ext_alt_hs300_idio_vol_40d_low | 是 | 是 | 是 | - |
| ext_alt_hs300_dividend_yield | 是 | 是 | 是 | - |
| ext_alt_hs300_vol_of_vol_20d_low | 是 | 否 | 否 | research_admission_missing |
| ext_alt_hs300_cash_flow_stability_4q_low | 是 | 是 | 是 | - |
| ext_alt_hs300_fund_holding_ratio_low | 是 | 是 | 是 | - |
| ext_alt_hs300_ocf_margin_4q | 是 | 否 | 否 | research_admission_missing |
| ext_alt_hs300_margin_chg_1m_low | 是 | 否 | 否 | research_admission_missing |
| ext_alt_hs300_holder_top10_share_chg_yoy | 是 | 否 | 否 | research_admission_missing |

- 双准入因子数：4
- 双准入名单：ext_alt_hs300_cash_flow_stability_4q_low, ext_alt_hs300_dividend_yield, ext_alt_hs300_fund_holding_ratio_low, ext_alt_hs300_idio_vol_40d_low

## 边界

- R438 admission is based on 60-day marginal IC for cash_flow_stability.
- ocf_margin remains unadmitted.
- No new backtest is run.
