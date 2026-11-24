# R533 因子库核心正式组合验证

## 训练期执行参数

| 持仓数 | 持有期 | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 10 | 20 | 0.1491 | 1.06 | -0.1134 | 0.005588015072686137 | 347 | True |
| 10 | 30 | 0.1468 | 1.04 | -0.1062 | 0.027456986098736813 | 242 | True |
| 10 | 60 | 0.1991 | 1.32 | -0.1067 | 0.15171369600831008 | 135 | True |
| 20 | 20 | 0.1619 | 1.11 | -0.1 | 0.03898166346993692 | 691 | True |
| 20 | 30 | 0.1422 | 1.01 | -0.0975 | 0.011233026221174613 | 482 | True |
| 20 | 60 | 0.1984 | 1.35 | -0.0996 | 0.13869274928384012 | 275 | True |
| 30 | 20 | 0.1602 | 1.11 | -0.1042 | 0.030692022867636393 | 1043 | True |
| 30 | 30 | 0.1684 | 1.17 | -0.09 | 0.056799797451656975 | 742 | True |
| 30 | 60 | 0.188 | 1.28 | -0.0925 | 0.09464871381540685 | 427 | True |

## 选中参数

- 持仓数：10
- 最大持有期：60
- 训练年化：0.1991
- 训练 Sharpe：1.32
- 训练 bootstrap p05：0.15171369600831008

## 确认窗口

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 0.1433 | 1.03 | -0.1449 | 0.7882 | 221 |
| full 3x | 0.1353 | 0.98 | -0.1473 | 0.7345 | 221 |
| full 5x | 0.1197 | 0.88 | -0.1517 | 0.6337 | 221 |
| validation 2x | 0.0163 | 0.2 | -0.055 | 0.012 | 42 |
| recent 2x | -0.047 | -0.32 | -0.2023 | -0.0482 | 59 |
| recent 3x | -0.0543 | -0.38 | -0.2049 | -0.0558 | 59 |
| recent 5x | -0.0687 | -0.5 | -0.2103 | -0.0706 | 59 |
| control full 2x | 0.128 | 0.98 | -0.1733 | 0.6868 | 224 |
| control recent 2x | -0.0369 | -0.28 | -0.1589 | -0.0379 | 57 |

## 年度和起始相位

### 年度

- 2022: 0.061834
- 2023: 0.077158
- 2024: 0.387876
- 2025: 0.078459
- 2026: 0.044553

### Fresh starts

- 2022-05-05: 0.7882
- 2023-01-03: 0.6163
- 2024-01-02: 0.6058
- 2025-01-02: 0.0282
- 2025-10-14: -0.0482
- 2026-01-05: -0.0391

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.annual_return', 'core_2x.max_drawdown', 'core_2x.recent_return', 'control_2x.annual_return', 'stress.annual_return', 'stress.max_drawdown', 'stress.recent_return']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- R533 attempt 1 failed before result publication because the prepared requested universe was replaced by filtered market axes; R533A is the explicit amendment.
- Factor parameters and the six-member core are frozen from R530/R531; R532 retained a conservative statistical-screen rejection.
- The R526 membership and industry vintages are reconstructed research inputs, not provider publication snapshots.
- Execution parameters are selected only on the training window, then validation and recent windows are confirmation-only.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or a hard rejection.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.annual_return
- core_2x.max_drawdown
- core_2x.recent_return
- control_2x.annual_return
- stress.annual_return
- stress.max_drawdown
- stress.recent_return
