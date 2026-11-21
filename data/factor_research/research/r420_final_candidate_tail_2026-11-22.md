# R420 四因子 vs 八因子尾部审计

| 候选 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 | 近一年年化 | 检验p05 | 近一年p05 | 检验MDD p05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| compact_4f | 21.03% | 25.98% | 6.02% | 0.530 | -12.06% | 5.00% | 4.63% | -13.98% | -12.74% | -23.19% |
| current_8f | 17.85% | 27.57% | 7.06% | 0.630 | -13.28% | 5.82% | 8.09% | -12.12% | -10.03% | -22.11% |

- 判定：**拒绝**

## 门槛

- compact_test_total_p05_positive: 未过
- compact_recent_total_p05_positive: 未过
- compact_test_mdd_p05_not_worse: 未过
- compact_recent_sharpe_p50_min: 通过

## 近一年月度（四因子）

| 月份 | 收益 |
| --- | ---: |
| 2025-10 | 0.00% |
| 2025-11 | 3.30% |
| 2025-12 | -1.40% |
| 2026-01 | -4.65% |
| 2026-02 | 1.61% |
| 2026-03 | 3.96% |
| 2026-04 | -0.42% |
| 2026-05 | -2.05% |
| 2026-06 | -7.92% |
| 2026-07 | 14.99% |
| 2026-08 | -3.29% |
| 2026-09 | 1.21% |
| 2026-10 | -0.92% |
| 2026-11 | 0.00% |

## 边界

- Bootstrap resamples historical paths; it does not create new market regimes.
- The test/recent windows have been observed before.
- Current HS300 membership applied to history retains survivorship bias.
- No leverage and five-x is warning-only.
