# R503 R499 滚动一年与市场分段确认

本轮冻结 R499 权重、弱市阈值与执行参数，只改变起始日期。

- 滚动一年正收益：15/15
- 滚动一年正超额：12/15
- 中位滚动收益：21.56%
- 最差滚动 MDD：-11.30%
- 自然年正收益：5/5
- 近一年绝对 p05：-7.85%
- 近一年超额 p05：-15.92%
- 判定：**拒绝**

## 门槛

- rolling_positive_rate_min: 通过
- rolling_relative_positive_rate_min: 通过
- rolling_median_total_positive: 通过
- rolling_median_relative_positive: 通过
- worst_rolling_mdd_min: 通过
- calendar_positive_years_min: 通过
- recent_absolute_p05_positive: 未过
- recent_paired_excess_p05_positive: 未过
- no_leverage: 通过

## 边界

- Rolling starts overlap heavily and are not independent samples.
- The 2022-05 start is the model training start and is not an independent holdout.
- The test/recent windows have been observed before.
- Regime labels are historical diagnostics, not a real-time vintage.
- Current HS300 membership and industry classification retain survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
