# R558 可直接回测策略参数 Monte Carlo

> 训练选参、验证确认、近期只披露；固定 10 仓、60 日、-12% 止损、14% 波动目标。

## 选中参数

- entry_score：80.0
- top_rank：30
- max_industry_positions：2
- 训练年化：19.64%
- 验证年化：0.75%

## 选中结果

- 全周期 2x 年化：13.70%
- 全周期 2x Sharpe：1.13
- 全周期 2x MDD：-11.58%
- 近期 2x 收益：4.16%
- 3x 全周期年化：13.11%

## 门槛

- full_annual_min: 通过
- full_sharpe_min: 通过
- full_mdd_min: 通过
- validation_annual_positive: 通过
- stress_3x_annual_min: 通过
- recent_return_positive: 通过
- fresh_positive_min: 通过
- fresh_median_positive: 通过
- long_phase_positive_min: 通过
- long_phase_median_positive: 通过

## 边界

- R558 is a post-R557 parameter search and is not an independent sample.
- The ready strategy's rank factor had negative standalone validation IC in R556B.
- Recent starts are disclosed only and are excluded from selection.
- GBM is a risk-mechanics null and cannot prove real financial alpha.
- No active version, orders, paper targets or production enablement are produced.
