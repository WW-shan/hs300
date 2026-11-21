# R421 逆波动权重尾部审计

| 权重 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 | 检验MDD p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| equal | 16.32% | 15.48% | 7.06% | 0.630 | -13.28% | 8.09% | -12.60% | -9.50% | -22.63% |
| inverse_vol | 16.32% | 15.48% | 7.06% | 0.630 | -13.28% | 8.09% | -12.60% | -9.50% | -22.63% |

- 判定：**拒绝**

## 门槛

- weight_variation_exists: 未过
- test_total_p05_not_worse: 通过
- recent_total_p05_not_worse: 通过
- test_sharpe_p50_not_worse: 通过
- test_mdd_p05_not_worse: 通过

## 逆波动权重

- ext_alt_hs300_idio_vol_40d_low: 12.50%
- ext_alt_hs300_dividend_yield: 12.50%
- ext_alt_hs300_vol_of_vol_20d_low: 12.50%
- ext_alt_hs300_cash_flow_stability_4q_low: 12.50%
- ext_alt_hs300_fund_holding_ratio_low: 12.50%
- ext_alt_hs300_ocf_margin_4q: 12.50%
- ext_alt_hs300_margin_chg_1m_low: 12.50%
- ext_alt_hs300_holder_top10_share_chg_yoy: 12.50%

## 边界

- Volatility is estimated on the training segment and may not transfer.
- Weights are not optimized on returns.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage; five-x warning only.
