# R479 沪深300 基准口径更正

R477 原 `benchmark_return` 实际来自引擎的上证指数 `000001.SH`。本轮只做 append-only 更正，不重跑、不重选策略。

| 窗口 | 策略总收益 | 沪深300 总收益 | 超额 | 原上证口径 |
| --- | ---: | ---: | ---: | ---: |
| test | 4.56% | 8.00% | -3.44% | 10.31% |
| recent | 11.43% | -9.38% | +20.81% | -5.36% |

## 结论

- 基准统一改为 `000300.SH`；R477 的历史上证口径仅作为旧标签保留。
- 校正不改变策略结论：R476/R477 仍为拒绝，`active_version` 仍为 null。

## 边界

- This correction does not rerun or improve the strategy.
- R477 remains rejected.
- The benchmark is price-only close-to-close and excludes dividends.
- No leverage and no production activation.
