# R555 行业中性 shrink 参数 Monte Carlo

> 训练选参、验证确认、近期只披露；R554 已观察全周期和近期，因此本轮最多 `watch_only`。

## 选中参数

- shrink：1.00
- 训练年化：9.00%
- 验证年化：6.30%

## 参数点

| shrink | 训练年化 | 验证年化 | 训练MDD | 平台 | 成本单调 |
| ---: | ---: | ---: | ---: | --- | --- |
| 0.00 | 17.41% | 2.42% | -9.32% | 是 | 是 |
| 0.25 | 18.42% | 2.42% | -9.62% | 是 | 是 |
| 0.50 | 21.93% | 3.71% | -9.03% | 是 | 是 |
| 0.75 | 19.78% | -1.39% | -8.18% | 否 | 是 |
| 1.00 | 9.00% | 6.30% | -12.61% | 是 | 是 |

## 选中结果

- 全周期 2x 年化：8.63%
- 全周期 2x Sharpe：0.74
- 全周期 2x MDD：-12.61%
- 近期 2x 收益：10.33%
- 3x 全周期年化：8.07%
- 5x 近期年化：7.99%（仅警告）

## 门槛

- full_annual_min: 未过
- full_sharpe_min: 未过
- full_mdd_min: 通过
- validation_annual_positive: 通过
- stress_3x_annual_min: 通过
- recent_return_positive: 通过
- fresh_positive_min: 通过
- fresh_median_positive: 通过
- long_phase_positive_min: 通过
- long_phase_median_positive: 通过

## 边界

- R555 was designed after R554 observations, so recent/full results are not independent replications.
- Recent starts are disclosed only and are excluded from parameter ranking.
- The selected variant remains watch-only with active_version=null and no orders.
- GBM is a risk-mechanics null and cannot prove financial alpha.
- 5x cost is warning-only; 2x is the baseline and 3x is the stress gate.
