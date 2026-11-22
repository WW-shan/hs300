# R517 行业历史数据可行性审计

- ETF 标的数：1770
- ETF 长历史数：6
- 指数标的数：562
- 指数长历史数：5
- ETF 可识别行业标签：0
- 指数可识别行业标签：0
- 判定：**拒绝**

## ETF 长历史

| 代码 | 名称 | 天数 | 起止 | 中位成交额 |
| --- | --- | ---: | --- | ---: |
| 510880.SH | 华泰柏瑞上证红利ETF | 2155 | 2018-01-02 → 2026-11-20 | 223832581 |
| 510300.SH | 华泰柏瑞沪深300ETF | 2155 | 2018-01-02 → 2026-11-20 | 2206195773 |
| 510500.SH | 南方中证500ETF | 2155 | 2018-01-02 → 2026-11-20 | 1301831939 |
| 518880.SH | 华安黄金ETF | 2155 | 2018-01-02 → 2026-11-20 | 1224640139 |
| 511010.SH | 国泰上证5年期国债ETF | 2155 | 2018-01-02 → 2026-11-20 | 184770287 |
| 512100.SH | 南方中证1000ETF | 2154 | 2018-01-02 → 2026-11-20 | 294465870 |

## 指数长历史

| 代码 | 名称 | 天数 | 起止 |
| --- | --- | ---: | --- |
| 000300.SH | 沪深300 | 2154 | 2018-01-02 → 2026-11-20 |
| 000905.SH | 中证500 | 2133 | 2018-01-02 → 2026-10-22 |
| 000001.SH | 上证指数 | 2133 | 2018-01-02 → 2026-10-22 |
| 000852.SH | 中证1000 | 2133 | 2018-01-02 → 2026-10-22 |
| 399300.SZ | 沪深300 | 2133 | 2018-01-02 → 2026-10-22 |

## 门槛

- etf_long_history_count: 未过
- index_long_history_count: 未过
- etf_distinct_industries: 未过
- index_distinct_industries: 未过
- etf_liquidity_positive: 通过

## 结论

本地行业 ETF/指数历史不足以支持 2018 年起的严格行业轮动。行业 ETF 大多只有约 250 天，长期标的主要是宽基、红利、债券和黄金。

## 边界

- Only local data are inspected; no network fetch is performed.
- An ETF name containing an industry keyword is not proof of a stable tracking index.
- Current instrument metadata is not historical constituent metadata.
- No leverage and no production activation.
