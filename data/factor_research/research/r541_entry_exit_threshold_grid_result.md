# R541 扩展组合入场/退出阈值网格

## 训练网格

| 入场 | 退出 | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 60 | 30 | 0.2456 | 1.54 | -0.1007 | 0.25636248767201697 | 119 | True |
| 60 | 40 | 0.3154 | 1.81 | -0.1072 | 0.4144589626142028 | 118 | True |
| 60 | 50 | 0.2238 | 1.41 | -0.1178 | 0.21076307168966069 | 119 | True |
| 70 | 30 | 0.2456 | 1.54 | -0.1007 | 0.27516212068604595 | 119 | True |
| 70 | 40 | 0.3154 | 1.81 | -0.1072 | 0.409270738401289 | 118 | True |
| 70 | 50 | 0.2238 | 1.41 | -0.1178 | 0.2156382577487761 | 119 | True |
| 80 | 30 | 0.2456 | 1.54 | -0.1007 | 0.2564561065423412 | 119 | True |
| 80 | 40 | 0.3154 | 1.81 | -0.1072 | 0.41050171988181905 | 118 | True |
| 80 | 50 | 0.2238 | 1.41 | -0.1178 | 0.19485824625361325 | 119 | True |

## 选中参数

- 入场：60
- 退出：40
- 训练年化：0.3154
- 训练 Sharpe：1.81
- bootstrap p05：0.4144589626142028

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
| default full 2x | 0.1672 | 1.14 | -0.1908 | 0.9568 | 196 |
| default recent 2x | -0.0088 | -0.01 | -0.1881 | -0.0091 | 53 |

## Fresh starts

- 2022-05-05: 0.9568
- 2023-01-03: 0.5266
- 2024-01-02: 0.3964
- 2025-01-02: 0.127
- 2025-10-14: -0.0091
- 2026-01-05: -0.0225

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.recent_return', 'recent_excess_vs_default']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- The 13 factor members, execution parameters, stop and industry cap are frozen from R539C.
- Entry/exit thresholds are selected only on the training window.
- Recent-year results have been observed repeatedly and are not an independent discovery sample.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- R526 membership and industry vintages are reconstructed research inputs.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.recent_return
- recent_excess_vs_default
