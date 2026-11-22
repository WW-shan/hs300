# R492 配对超额 Bootstrap

本轮把 R490 的绝对收益尾部和相对沪深300 的超额尾部拆开，严格按共同交易日配对。

| 窗口 | 配对天数 | 块长 | 绝对收益 p05 | 超额 p05 | 绝对中位 | 超额中位 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test | 365 | 10 | -6.54% | -27.74% | 24.45% | 10.68% |
| test | 365 | 20 | -6.02% | -30.22% | 23.91% | 9.94% |
| test | 365 | 40 | -5.80% | -33.67% | 24.19% | 10.93% |
| recent | 257 | 10 | -13.38% | -20.12% | 10.32% | 17.40% |
| recent | 257 | 20 | -10.98% | -19.82% | 10.04% | 16.55% |
| recent | 257 | 40 | -9.19% | -17.85% | 10.26% | 17.21% |

## 结论

- 绝对 p05 与超额 p05 分开报告；不能用超额稳健掩盖绝对亏损风险。
- R490 仍为 rejected，`active_version` 保持 null。

## 边界

- Paired bootstrap resamples historical joint paths only.
- Benchmark is price-only close-to-close and excludes dividends.
- R490 remains rejected regardless of this diagnostic.
- No leverage, orders or production activation.
