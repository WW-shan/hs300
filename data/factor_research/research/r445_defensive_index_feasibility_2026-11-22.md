# R445 防御指数数据可行性

| 指数 | 行数 | 首日 | 末日 | 与300重叠 | 缺失300交易日 |
| --- | ---: | --- | --- | ---: | ---: |
| 000300.SH | 2154 | 2018-01-02 | 2026-11-20 | 2154 | 0 |
| 000016.SH | 5 | 2026-10-16 | 2026-10-22 | 5 | 2149 |
| 000905.SH | 2133 | 2018-01-02 | 2026-10-22 | 2133 | 21 |
| 000852.SH | 2133 | 2018-01-02 | 2026-10-22 | 2133 | 21 |
| 399006.SZ | 5 | 2026-10-16 | 2026-10-22 | 5 | 2149 |
| 000015.SH | 5 | 2026-10-16 | 2026-10-22 | 5 | 2149 |
| 000012.SH | 5 | 2026-10-16 | 2026-10-22 | 5 | 2149 |

- 判定：**部分数据不可用**
- ETF 本地历史仅 268 天，本审计使用指数长历史，不等同可交易 ETF 净值。

## 边界

- Index levels are not executable ETF NAVs; ETF tracking and fees are not modeled here.
- ETF local history starts only 2025-10-16 and is insufficient for long walk-forward.
- No gold/overseas index is present in the local index catalog.
- No strategy rule is selected in this audit.
