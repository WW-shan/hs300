# R554 行业中性 cash_safety 正式确认

> 固定 R552C 参数，在项目 HS300 数据链上比较原始评分与行业中性评分；近期仅披露，不参与变体选择。

## 固定参数

- 持仓：10
- 持有：60 日
- 波动目标：14%
- 止损：-12%
- 行业上限：3

## 结果

| 口径 | 区间 | 收益 | 年化 | Sharpe | MDD | 交易 | 超额 HS300 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 行业中性 | 2022-05-05 → 2026-11-05 | 43.24% | 8.63% | 0.74 | -12.61% | 233 | 40.67% |
| 原始对照 | 2022-05-05 → 2026-11-05 | 57.90% | 11.10% | 0.92 | -14.43% | 195 | 55.33% |
| 行业中性 | 2025-01-01 → 2025-10-13 | 4.61% | 6.30% | 0.47 | -9.84% | 40 | -12.14% |
| 原始对照 | 2025-01-01 → 2025-10-13 | 1.78% | 2.42% | 0.26 | -6.56% | 40 | -14.97% |
| 行业中性 | 2025-10-14 → 2026-11-05 | 10.33% | 10.04% | 0.81 | -7.31% | 66 | 19.71% |
| 原始对照 | 2025-10-14 → 2026-11-05 | 4.34% | 4.22% | 0.41 | -12.15% | 52 | 13.72% |

## 稳健性

- 新鲜起点：3/5 为正，中位 3.45%。
- 长阶段起点：10/10 为正，中位 30.21%。
- 3x 全周期年化：8.07%。
- 5x 近期年化：7.99%（仅警告）。

## 门槛

- full_annual_min: 未过
- full_sharpe_min: 未过
- full_mdd_min: 通过
- validation_annual_positive: 通过
- validation_beats_control: 通过
- stress_3x_annual_min: 通过
- recent_return_positive: 通过
- fresh_positive_min: 通过
- fresh_median_positive: 通过
- long_phase_positive_min: 通过
- long_phase_median_positive: 通过

## GBM

- 路径数：100
- 约束错误：[]

## 边界

- Recent starts have already been observed in R550/R552C and are not independent OOS data.
- The strategy is still watch-only, with active_version=null and no orders or paper targets.
- Industry labels are the project PIT industry mapping; classification errors remain a data risk.
- GBM prices contain no financial signal, so GBM can validate risk mechanics but cannot prove real alpha.
- 5x cost is warning-only; 2x is the baseline and 3x is the stress gate.
