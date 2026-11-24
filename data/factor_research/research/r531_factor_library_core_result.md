# R531 因子库核心独立性与综合能力

## 输入

- R530 准入候选：11
- 训练期月末相关截面：32
- 相关性阈值：0.7

## 独立性与代表因子

| 聚类 | 代表因子 | 训练期 IC | 聚类大小 |
| --- | --- | ---: | ---: |
| amount_level | amount_level | 0.023543 | 1 |
| annual_volatility, atr_risk, bollinger_width, cs_spread, idio_vol | atr_risk | 0.035085 | 5 |
| dividend_yield_absolute, dividend_yield_quantile | dividend_yield_absolute | 0.0339 | 2 |
| max_return | max_return | 0.042375 | 1 |
| turnover_level | turnover_level | 0.022265 | 1 |
| vol_of_vol | vol_of_vol | 0.031098 | 1 |

## 核心 rank 因子

- full IC：0.041368
- validation IC：0.029096
- recent IC：0.052005
- coverage：1.0
- bootstrap 10/20/40 CI low：0.02385472/0.02300673/0.02392857

## 结论

- 总体结论：admitted_for_research
- 门槛：{'at_least_two_clusters': True, 'representatives_selected': True, 'coverage_ge_0_90': True, 'full_ic_ge_0_01': True, 'train_ic_ge_0_01': True, 'validation_ic_positive': True, 'recent_ic_positive': True, 'bootstrap_10_20_40_ci_positive': True}

## 边界

- Clusters are selected only from training-period month-end factor ranks.
- The core is a research factor rank, not a portfolio allocation or return stream.
- Correlation clustering is threshold-sensitive; 0.70 was frozen before validation results.
- R526 membership and industry vintages are reconstructed research inputs.
- No leverage, orders, paper targets or active version are produced.
