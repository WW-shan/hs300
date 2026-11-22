# R528 统一 PIT 家族对照

- R526 bundle：哈希全部通过。
- 同一名单输入：两家族都由框架掩码使用 `daily_snapshots.parquet`；R527 的 five-leg 同名单声明无效。
- 行业中性化：两家族没有共用同一套行业口径；R526 的申万时点表仅作审计输入。

## 冻结家族结果

| 家族 | 窗口 | 年化 | Sharpe | MDD | 总收益 | p05 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| hold30 | test | 16.98% | 1.572 | -7.36% | 25.59% | 5.48% |
| hold30 | recent | 8.75% | 0.796 | -7.36% | 8.97% | -5.30% |
| five_leg | test | 18.19% | 1.396 | -9.74% | 27.47% | -1.32% |
| five_leg | recent | 18.39% | 1.374 | -9.74% | 18.87% | -5.38% |

- 点估计领先：**five_leg**。
- 合格赢家：**无**。
- 检验期 hold30 - five_leg 年化：-1.20%。
- 检验期 hold30 - five_leg Sharpe：+0.176。

## 判定边界

- This is a comparison of two frozen families, not a new alpha search.
- Both families receive the same frozen R526 membership input.
- The R526 Shenwan industry_pit table is an audit input; it is not the THS industry_l1 mapping used by score neutralization.
- R385/R386 family construction still depends on local research caches and strategy files; their hashes are recorded.
- R525's original membership-vintage causal question is only partly answered by this comparison.
- No leverage and no production activation.
- R527 remains an artifact; R528 reruns both families without changing frozen definitions.
- The five-leg extension fields were frozen from the local alt_hs300 timeseries into a small committed bundle.
- R527 remains stored as an invalidated historical comparison; R528 supersedes only its membership-parity claim.
