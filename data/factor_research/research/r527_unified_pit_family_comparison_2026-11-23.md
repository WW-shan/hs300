# R527 统一 PIT 家族对照

- R526 bundle：哈希全部通过。
- 同一名单输入：两家族都使用 `daily_snapshots.parquet`。
- 行业中性化：两家族没有共用同一套行业口径；R526 的申万时点表仅作审计输入。

## 冻结家族结果

| 家族 | 窗口 | 年化 | Sharpe | MDD | 总收益 | p05 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| hold30 | test | 16.99% | 1.572 | -7.34% | 25.60% | 5.47% |
| hold30 | recent | 8.75% | 0.796 | -7.34% | 8.97% | -5.31% |
| five_leg | test | 10.88% | 0.868 | -11.35% | 16.19% | -9.71% |
| five_leg | recent | 15.11% | 1.114 | -11.35% | 15.50% | -5.86% |

- 点估计领先：**hold30**。
- 合格赢家：**无**。
- 检验期 hold30 - five_leg 年化：+6.11%。
- 检验期 hold30 - five_leg Sharpe：+0.704。

## 判定边界

- This is a comparison of two frozen families, not a new alpha search.
- Both families receive the same frozen R526 membership input.
- The R526 Shenwan industry_pit table is an audit input; it is not the THS industry_l1 mapping used by score neutralization.
- R385/R386 family construction still depends on local research caches and strategy files; their hashes are recorded.
- R525's original membership-vintage causal question is only partly answered by this comparison.
- No leverage and no production activation.
- The five-leg extension fields were frozen from the local alt_hs300 timeseries into a small committed bundle.
