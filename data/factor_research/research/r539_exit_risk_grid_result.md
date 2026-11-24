# R539 扩展组合退出风控网格

## 训练网格

| 变体 | stop | trailing | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| stop_05 | -0.05 | None | 0.2109 | 1.3 | -0.0968 | 0.13775999446699494 | 162 | True |
| stop_08 | -0.08 | None | 0.2791 | 1.61 | -0.1243 | 0.34512983888879156 | 128 | True |
| stop_12 | -0.12 | None | 0.3154 | 1.81 | -0.1072 | 0.41103031708241566 | 118 | True |
| stop_15 | -0.15 | None | 0.2949 | 1.76 | -0.1083 | 0.3776602762373289 | 115 | True |
| no_stop | None | None | 0.2876 | 1.74 | -0.1104 | 0.36153274922953793 | 112 | True |
| stop_08_trail_10 | -0.08 | -0.1 | 0.2393 | 1.5 | -0.1059 | 0.1897809608649672 | 147 | True |
| stop_12_trail_10 | -0.12 | -0.1 | 0.2164 | 1.36 | -0.1254 | 0.14874589701766094 | 146 | True |

## 选中变体

- 变体：`stop_12`
- stop：-0.12
- trailing：None
- 训练年化：0.3154
- 训练 Sharpe：1.81
- bootstrap p05：0.41103031708241566

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
| control full 2x | 0.16 | 1.07 | -0.1588 | 0.905 | 215 |
| control recent 2x | -0.0606 | -0.44 | -0.1693 | -0.0622 | 62 |

## Fresh starts

- 2022-05-05: 0.9568
- 2023-01-03: 0.5266
- 2024-01-02: 0.3964
- 2025-01-02: 0.127
- 2025-10-14: -0.0091
- 2026-01-05: -0.0225

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.recent_return']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- The 13 factor members, 10 positions, 60-day hold and industry cap are frozen from R538C.
- Exit parameters are selected only on the training window.
- Recent-year results have been observed repeatedly and are not an independent discovery sample.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- R526 membership and industry vintages are reconstructed research inputs.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.recent_return
