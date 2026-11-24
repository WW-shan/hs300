# R538 扩展因子组合执行参数与行业风控网格

## 训练网格

| 持仓 | 持有期 | 行业上限 | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 10 | 20 | 3 | 0.1933 | 1.28 | -0.1058 | 0.12480495621740162 | 337 | True |
| 10 | 30 | 3 | 0.2136 | 1.41 | -0.0929 | 0.1680619200141198 | 234 | True |
| 10 | 60 | 3 | 0.2791 | 1.61 | -0.1243 | 0.3497807109302207 | 128 | True |
| 20 | 20 | 6 | 0.1556 | 1.12 | -0.1093 | 0.051234232179768524 | 627 | True |
| 20 | 30 | 6 | 0.2153 | 1.46 | -0.097 | 0.18837668607876856 | 452 | True |
| 20 | 60 | 6 | 0.2217 | 1.42 | -0.1151 | 0.19622784223602596 | 257 | True |
| 30 | 20 | 9 | 0.1614 | 1.11 | -0.1053 | 0.03470957625096038 | 1010 | True |
| 30 | 30 | 9 | 0.1961 | 1.31 | -0.1009 | 0.1283747874287373 | 715 | True |
| 30 | 60 | 9 | 0.2298 | 1.46 | -0.1037 | 0.1953484066939779 | 397 | True |

## 选中参数

- 持仓：10
- 最大持有：60 日
- 行业上限：3
- 训练年化：0.2791
- 训练 Sharpe：1.61
- bootstrap p05：0.3497807109302207

## 确认结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 0.16 | 1.07 | -0.1588 | 0.905 | 215 |
| full 3x | 0.152 | 1.02 | -0.161 | 0.8482 | 215 |
| full 5x | 0.1367 | 0.93 | -0.1761 | 0.7442 | 215 |
| validation 2x | 0.0537 | 0.49 | -0.0522 | 0.0393 | 42 |
| recent 2x | -0.0606 | -0.44 | -0.1693 | -0.0622 | 62 |
| recent 3x | -0.0685 | -0.5 | -0.172 | -0.0703 | 62 |
| recent 5x | -0.0832 | -0.63 | -0.1786 | -0.0854 | 62 |
| core full 2x | 0.1358 | 0.99 | -0.187 | 0.738 | 232 |
| core recent 2x | -0.0517 | -0.36 | -0.205 | -0.0531 | 59 |

## Fresh starts

- 2022-05-05: 0.905
- 2023-01-03: 0.6961
- 2024-01-02: 0.4797
- 2025-01-02: 0.053
- 2025-10-14: -0.0622
- 2026-01-05: -0.0276

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.recent_return', 'stress.recent_return']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- Execution parameters are selected only on the training window; validation/recent are confirmation.
- The 13 factor members and parameters are frozen from R536A; no factor reselection occurs.
- Industry caps use the project current industry snapshot, not a full historical provider vintage.
- Recent-year results have been observed repeatedly and are not an independent discovery sample.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.recent_return
- stress.recent_return
