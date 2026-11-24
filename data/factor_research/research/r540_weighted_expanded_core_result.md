# R540 训练期加权扩展因子核心

## 训练方案

| 方案 | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| equal | 0.3154 | 1.81 | -0.1072 | 0.3978605908936266 | 118 | True |
| icir | 0.2511 | 1.55 | -0.123 | 0.27457765014626867 | 118 | True |
| top8_icir | 0.2126 | 1.38 | -0.1011 | 0.2035308782091272 | 116 | True |
| top5_icir | 0.1423 | 0.96 | -0.1024 | 0.011783068024546317 | 122 | True |

## 选中方案

- 方案：`equal`
- 训练年化：0.3154
- 训练 Sharpe：1.81
- bootstrap p05：0.3978605908936266

## 确认结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 0.1672 | 1.14 | -0.1908 | 0.9568 | 196 |
| full 3x | 0.1598 | 1.1 | -0.1991 | 0.9032 | 196 |
| full 5x | 0.1453 | 1.01 | -0.216 | 0.8021 | 196 |
| validation 2x | 0.067 | 0.59 | -0.0514 | 0.049 | 40 |
| recent 2x | -0.0088 | -0.01 | -0.1881 | -0.0091 | 53 |
| recent 3x | -0.0159 | -0.06 | -0.1912 | -0.0163 | 53 |
| recent 5x | -0.0295 | -0.17 | -0.1972 | -0.0303 | 53 |
| equal full 2x | 0.1672 | 1.14 | -0.1908 | 0.9568 | 196 |
| equal recent 2x | -0.0088 | -0.01 | -0.1881 | -0.0091 | 53 |

## Fresh starts

- 2022-05-05: 0.9568
- 2023-01-03: 0.5266
- 2024-01-02: 0.3964
- 2025-01-02: 0.127
- 2025-10-14: -0.0091
- 2026-01-05: -0.0225

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.recent_return', 'recent_excess_vs_equal']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- The 13 factor members are frozen from R536A; only their weights are varied.
- Weights use training ICIR only; validation/recent are confirmation.
- R536A's strict independent-cluster FDR rejected all 13 members; this is a portfolio combination study, not a reversal.
- Recent-year results have been observed repeatedly and are not an independent discovery sample.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.recent_return
- recent_excess_vs_equal
