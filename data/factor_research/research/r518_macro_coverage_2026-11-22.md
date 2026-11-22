# R518 宏观与资金条件变量覆盖审计

| 变量 | 日期数 | 首日 | 末日 | 近一年可用 | 全覆盖 |
| --- | ---: | --- | --- | --- | --- |
| `shibor_3m` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `shibor_3m_chg_20d` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `m1_m2_scissors` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `m1_m2_scissors_chg_1m` | 2128 | 2018-01-02 | 2026-10-14 | 否 | 是 |
| `m2_yoy` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `term_spread_10y_2y` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `credit_spread_5y_low` | 1900 | 2019-01-02 | 2026-11-05 | 是 | 是 |
| `equity_risk_premium` | 2143 | 2018-01-02 | 2026-11-05 | 是 | 是 |
| `equity_risk_premium_chg3m` | 2080 | 2018-04-10 | 2026-11-05 | 是 | 是 |
| `pmi` | 1898 | 2018-01-02 | 2025-10-30 | 否 | 是 |
| `cpi_yoy` | 1882 | 2018-01-02 | 2025-09-30 | 否 | 是 |
| `ppi_yoy` | 1882 | 2018-01-02 | 2025-09-30 | 否 | 是 |
| `northbound_net_buy_20d` | 1609 | 2018-01-02 | 2024-08-16 | 否 | 否 |
| `margin_chg_1m_low` | 1316 | 2021-06-01 | 2026-11-05 | 是 | 否 |
| `etf_share_chg_1m` | 1393 | 2021-02-01 | 2026-11-05 | 是 | 否 |
| `etf_share_chg_3m` | 1355 | 2021-04-01 | 2026-11-05 | 是 | 否 |
| `if_net_short_chg_1m` | 1364 | 2021-03-01 | 2026-10-16 | 否 | 否 |

## 可用条件变量

- 全长+近期完整：credit_spread_5y_low, equity_risk_premium, equity_risk_premium_chg3m, m1_m2_scissors, m2_yoy, shibor_3m, shibor_3m_chg_20d, term_spread_10y_2y
- 部分或过期：cpi_yoy, etf_share_chg_1m, etf_share_chg_3m, if_net_short_chg_1m, m1_m2_scissors_chg_1m, margin_chg_1m_low, northbound_net_buy_20d, pmi, ppi_yoy

## 边界

- Coverage is not alpha.
- Macro release timing is represented by the local effective-date fields.
- No leverage and no production activation.
