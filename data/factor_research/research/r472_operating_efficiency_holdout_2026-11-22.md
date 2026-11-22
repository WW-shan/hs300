# R472 营运效率 sleeve fresh-start 留出审计

R468 的高检验收益需要检查是否依赖固定起点。本轮固定 R468 参数，只改变 fresh-start 起点，不重新选参。

- 源：`inventory_growth_minus_cogs_growth|p10|h20|e70|x30`
- 相位：5，正收益：5
- 判定：**拒绝**

| 起点 | 总收益 | 年化 | Sharpe | MDD | p05 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2022-05-05 | 175.74% | 26.35% | 1.236 | -17.53% | 19.36% |
| 2023-05-04 | 175.74% | 35.08% | 1.403 | -17.53% | 23.31% |
| 2024-05-06 | 139.74% | 43.68% | 1.592 | -16.76% | 17.59% |
| 2025-05-06 | 59.53% | 37.93% | 1.735 | -12.37% | 2.00% |
| 2025-10-14 | 7.86% | 7.67% | 0.489 | -12.84% | -19.80% |

## 门槛

- all_phases_positive: 通过
- minimum_phase_total_positive: 通过
- minimum_phase_sharpe_positive: 通过
- all_bootstrap_p05_positive: 未过
- no_leverage: 通过

## 边界

- This is a fixed-parameter fresh-start audit, not a new parameter search.
- The factor history starts in 2023-05-04; earlier fresh starts have lower exposure.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage and no production activation.
