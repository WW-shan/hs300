# R427 状态动态权重审计

| 版本 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| static | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 8.09% |
| dynamic | 17.87% | 29.90% | 3.66% | 0.360 | -15.36% | 3.12% |

- 最大权重偏离等权：10.50%
- 判定：**拒绝**

## 门槛

- weight_variation_exists: 通过
- test_total_p05_not_worse: 未过
- recent_total_p05_not_worse: 未过

## 状态权重

- weak: ext_alt_hs300_idio_vol_40d_low=18.5%, ext_alt_hs300_dividend_yield=11.1%, ext_alt_hs300_vol_of_vol_20d_low=16.1%, ext_alt_hs300_cash_flow_stability_4q_low=16.4%, ext_alt_hs300_fund_holding_ratio_low=8.8%, ext_alt_hs300_ocf_margin_4q=4.4%, ext_alt_hs300_margin_chg_1m_low=12.9%, ext_alt_hs300_holder_top10_share_chg_yoy=11.8%
- lean_weak: ext_alt_hs300_idio_vol_40d_low=19.3%, ext_alt_hs300_dividend_yield=22.0%, ext_alt_hs300_vol_of_vol_20d_low=16.2%, ext_alt_hs300_cash_flow_stability_4q_low=9.8%, ext_alt_hs300_fund_holding_ratio_low=13.8%, ext_alt_hs300_ocf_margin_4q=10.8%, ext_alt_hs300_margin_chg_1m_low=2.0%, ext_alt_hs300_holder_top10_share_chg_yoy=5.9%
- range: ext_alt_hs300_idio_vol_40d_low=18.8%, ext_alt_hs300_dividend_yield=18.4%, ext_alt_hs300_vol_of_vol_20d_low=10.8%, ext_alt_hs300_cash_flow_stability_4q_low=12.2%, ext_alt_hs300_fund_holding_ratio_low=12.6%, ext_alt_hs300_ocf_margin_4q=8.9%, ext_alt_hs300_margin_chg_1m_low=14.1%, ext_alt_hs300_holder_top10_share_chg_yoy=4.1%
- lean_strong: ext_alt_hs300_idio_vol_40d_low=22.2%, ext_alt_hs300_dividend_yield=18.1%, ext_alt_hs300_vol_of_vol_20d_low=20.2%, ext_alt_hs300_cash_flow_stability_4q_low=4.8%, ext_alt_hs300_fund_holding_ratio_low=16.0%, ext_alt_hs300_ocf_margin_4q=2.0%, ext_alt_hs300_margin_chg_1m_low=14.3%, ext_alt_hs300_holder_top10_share_chg_yoy=2.4%
- strong: ext_alt_hs300_idio_vol_40d_low=20.2%, ext_alt_hs300_dividend_yield=18.1%, ext_alt_hs300_vol_of_vol_20d_low=16.0%, ext_alt_hs300_cash_flow_stability_4q_low=9.3%, ext_alt_hs300_fund_holding_ratio_low=17.5%, ext_alt_hs300_ocf_margin_4q=7.1%, ext_alt_hs300_margin_chg_1m_low=9.8%, ext_alt_hs300_holder_top10_share_chg_yoy=2.0%

## 边界

- Regime labels are lagged but not a full real-time vintage audit.
- Weights are estimated only on the training segment.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
- Forward-fill is used only for the short tail where regime_history is not yet available; it must be re-audited when regime history extends.
