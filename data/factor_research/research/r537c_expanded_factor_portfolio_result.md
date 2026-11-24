# R537 13 独立代表扩展因子组合

- 代表数：13
- 构造：13 因子等权 rank；control：R533 6 因子核心

## 确认结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 0.1276 | 0.92 | -0.1494 | 0.6846 | 215 |
| full 3x | 0.1199 | 0.87 | -0.1548 | 0.6349 | 215 |
| full 5x | 0.1048 | 0.77 | -0.1649 | 0.5415 | 215 |
| validation 2x | 0.1019 | 0.83 | -0.0565 | 0.0743 | 41 |
| recent 2x | -0.0177 | -0.08 | -0.1661 | -0.0182 | 60 |
| recent 3x | -0.0255 | -0.14 | -0.1686 | -0.0262 | 60 |
| recent 5x | -0.0413 | -0.26 | -0.1758 | -0.0425 | 60 |
| core full 2x | 0.1433 | 1.03 | -0.1449 | 0.7882 | 221 |
| core recent 2x | -0.047 | -0.32 | -0.2023 | -0.0482 | 59 |

## 年度和起始相位

- 2022: 0.038649
- 2023: 0.15438
- 2024: 0.345513
- 2025: 0.018993
- 2026: 0.024764
- fresh 2022-05-05: 0.6846
- fresh 2023-01-03: 0.6933
- fresh 2024-01-02: 0.5549
- fresh 2025-01-02: 0.1176
- fresh 2025-10-14: -0.0182
- fresh 2026-01-05: -0.0332

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.sharpe', 'core_2x.recent_return', 'control_2x.sharpe', 'stress.recent_return', 'concentration.top_industry_gross_share']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False

## 边界

- R537 attempts 1-3 failed before any backtest due runtime-path and R531 wrapper mismatches; R537C is the explicit amendment.
- All 13 representatives were rejected by the strict R536A independent-cluster FDR; this is a portfolio-level combination test, not a reversal of that conclusion.
- The recent year has been observed repeatedly; R537 is not an independent discovery sample.
- Only the 10-position/60-day execution from R533A is used; no new parameter selection is performed.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- R526 membership and industry vintages are reconstructed research inputs.
- No leverage, orders, paper targets or active version are produced.
