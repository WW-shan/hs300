# R522 R506 fresh-start 一年审计

- 冻结 R506，不重新选样。
- 窗口数：5；完整一年：2
- 绝对正收益：5/5
- 最差 MDD：-10.46%
- 最差 bootstrap p05：-10.70%
- 判定：**拒绝**

| 起点 | 类型 | 年化 | Sharpe | MDD | 超额 | 3×年化 | p05 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2025-10-14 | full_year | 15.42% | 1.170 | -8.89% | +26.35% | 13.76% | -4.35% |
| 2025-11-14 | full_year | 11.28% | 0.880 | -9.45% | +23.05% | 9.64% | -8.75% |
| 2025-12-15 | partial | 8.91% | 0.700 | -10.31% | +17.76% | 7.21% | -10.70% |
| 2026-01-14 | partial | 14.33% | 1.060 | -9.82% | +24.52% | 12.64% | -6.64% |
| 2026-02-16 | partial | 14.56% | 1.080 | -10.46% | +21.46% | 12.89% | -7.99% |

## 门槛

- all_windows_positive_absolute: 通过
- all_windows_positive_excess: 通过
- worst_window_mdd_min: 通过
- stress_3x_positive_all: 通过
- worst_bootstrap_total_p05_positive: 未过
- no_leverage: 通过

## 边界

- Fresh-start one-year windows overlap heavily and are not independent samples.
- R506 was already selected and rejected by its full gate set; this audit does not promote it.
- The test/recent windows have been observed before.
- Current HS300 membership retains survivorship bias.
- 5x cost is warning-only and never a hard rejection.
- No leverage and no production activation.
