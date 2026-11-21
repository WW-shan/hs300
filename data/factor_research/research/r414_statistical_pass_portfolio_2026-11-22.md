# R414 R391统计通过因子组合确认

- 选中 8 腿：ext_alt_hs300_short_ratio_low, ext_alt_hs300_fund_holding_ratio_low, inventory_turnover_improvement_yoy, ext_alt_hs300_dividend_yield, ext_alt_hs300_idio_vol_40d_low, ext_alt_hs300_vol_of_vol_60d_low, ext_alt_hs300_lhb_event_count_20d_low, ext_alt_hs300_holder_top10_share_chg_yoy
| 版本 | 训练年化 | 训练Sharpe | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 当前基线 | 19.93% | 1.310 | 7.06% | 0.630 | -13.28% | - |
| R391通过者子集 | 0.47% | 0.100 | 7.78% | 0.670 | -16.26% | 6.12% |

- 判定：**拒绝**

## 门槛

- selected_has_8: 通过
- train_annual_min: 未过
- test_annual_min: 通过
- test_sharpe_min: 通过
- test_mdd_min: 通过
- stress_3x_positive: 通过
- beat_baseline_train: 未过
- beat_baseline_test: 通过

## 边界

- R391 candidates are 5-day statistical passes, not guaranteed portfolio alpha.
- Subset selection is training-only; test is confirmation only.
- Current HS300 membership applied to history retains survivorship bias.
- The test window has been observed before.
- No leverage and 5x is warning-only.
