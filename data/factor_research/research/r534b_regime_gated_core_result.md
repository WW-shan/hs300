# R534B 因子库核心市场情绪/状态门控

冻结 R533 的六因子、10 仓、60 日持有；只改变 T-1 市场状态门控。

## 训练期规则

| 规则 | 年化 | Sharpe | MDD | bootstrap MDD p05 | bootstrap total p05 | 交易数 | 资格 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| always_on | 0.1991 | 1.32 | -0.1067 | -0.18327146579076914 | 0.14902991205692873 | 135 | True |
| exclude_weak | 0.1994 | 1.38 | -0.1071 | -0.1870728130806958 | 0.14313776510523546 | 135 | True |
| exclude_weak_pair | 0.2045 | 1.37 | -0.101 | -0.18130682182518243 | 0.15612812057731507 | 127 | True |
| score_min_20 | 0.1816 | 1.26 | -0.1044 | -0.19182897327053527 | 0.10452035966613193 | 131 | True |
| score_min_30 | 0.1994 | 1.38 | -0.1071 | -0.18834786056316688 | 0.15109088556482964 | 135 | True |
| exclude_weak_pair_min20 | 0.2045 | 1.37 | -0.101 | -0.18178699905983908 | 0.16462994072430504 | 127 | True |

## 选中规则

- 规则：`exclude_weak_pair`
- 训练年化：0.2045
- 训练 Sharpe：1.37
- 训练 MDD：-0.101
- bootstrap MDD p05：-0.18130682182518243

## 确认结果

| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| full 2x | 0.1227 | 0.92 | -0.1683 | 0.6407 | 210 |
| full 3x | 0.1152 | 0.87 | -0.1735 | 0.5944 | 210 |
| full 5x | 0.1003 | 0.77 | -0.1831 | 0.5054 | 210 |
| validation 2x | -0.0119 | -0.05 | -0.0611 | -0.0088 | 39 |
| recent 2x | -0.0952 | -0.76 | -0.1789 | -0.092 | 58 |
| recent 3x | -0.1031 | -0.83 | -0.1836 | -0.0996 | 58 |
| recent 5x | -0.1174 | -0.96 | -0.1917 | -0.1135 | 58 |
| base full 2x | 0.135 | 0.97 | -0.1449 | 0.7192 | 220 |
| base recent 2x | -0.086 | -0.65 | -0.2023 | -0.0831 | 58 |
| control full 2x | 0.122 | 0.95 | -0.1341 | 0.6363 | 219 |
| control recent 2x | -0.0751 | -0.68 | -0.1463 | -0.0725 | 54 |

## Fresh starts

- 2022-05-05: 0.6407
- 2023-01-03: 0.4962
- 2024-01-02: 0.468
- 2025-01-02: -0.0565
- 2025-10-14: -0.092
- 2026-01-05: -0.043

## 门禁

- 总体结论：rejected
- 失败路径：['core_2x.sharpe', 'core_2x.recent_return', 'core_2x.minimum_fresh_starts', 'stress.recent_return', 'validation_annual_positive', 'recent_excess_vs_always_on']
- v2 状态：rejected
- 5× warnings：[]
- 5× 硬否决：False
- 近一年相对 always-on：-0.0089

## 边界

- R534 attempts 1 and 2 failed before result publication due the runtime-helper and regime-coverage plumbing defects; R534B is the explicit amendment.
- The recent year was already observed in R533; R534A is post-hoc regime conditioning, not an independent discovery.
- Only T-1 regime state/score is used by the project engine; no same-day state is backfilled.
- The regime history ends on 2026-10-21, so confirmation does not extend to 2026-11-05.
- R526 membership and industry vintages are reconstructed research inputs.
- The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.
- No leverage, orders, paper targets or active version are produced.

## 负知识

- core_2x.sharpe
- core_2x.recent_return
- core_2x.minimum_fresh_starts
- stress.recent_return
- validation_annual_positive
- recent_excess_vs_always_on
