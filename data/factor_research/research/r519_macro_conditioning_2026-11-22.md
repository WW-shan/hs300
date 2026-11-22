# R519 宏观条件化 IC 审计

| 条件变量 | 股票袖 | 周期 | 高-低 IC | NW p | q | 高/低天数 | 门槛 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| shibor_3m | admitted_core | 5 | 0.04500878 | 0.81638777 | 0.92735396 | 207/595 | 拒绝 |
| shibor_3m | admitted_core | 20 | 0.05485766 | 0.70973573 | 0.8735209 | 207/580 | 拒绝 |
| shibor_3m | admitted_core | 60 | 0.11302087 | 0.84564968 | 0.92735396 | 207/540 | 拒绝 |
| shibor_3m | value_quality_sleeve | 5 | 0.02482818 | 0.63338922 | 0.80007059 | 207/595 | 拒绝 |
| shibor_3m | value_quality_sleeve | 20 | 0.04701304 | 0.91456917 | 0.94827669 | 207/580 | 拒绝 |
| shibor_3m | value_quality_sleeve | 60 | 0.10440009 | 0.52076359 | 0.6755852 | 207/540 | 拒绝 |
| shibor_3m_chg_20d | admitted_core | 5 | 0.03473659 | 0.05280174 | 0.23040759 | 269/241 | 拒绝 |
| shibor_3m_chg_20d | admitted_core | 20 | 0.06020197 | 0.05820229 | 0.23280916 | 269/241 | 拒绝 |
| shibor_3m_chg_20d | admitted_core | 60 | 0.09208699 | 0.08507304 | 0.26912457 | 269/241 | 拒绝 |
| shibor_3m_chg_20d | value_quality_sleeve | 5 | 0.00993229 | 0.32954923 | 0.49432385 | 269/241 | 拒绝 |
| shibor_3m_chg_20d | value_quality_sleeve | 20 | 0.01728635 | 0.44122416 | 0.6229047 | 269/241 | 拒绝 |
| shibor_3m_chg_20d | value_quality_sleeve | 60 | 0.04638803 | 0.32279691 | 0.49432385 | 269/241 | 拒绝 |
| m1_m2_scissors | admitted_core | 5 | -0.03325372 | 0.02174529 | 0.13047174 | 415/457 | 拒绝 |
| m1_m2_scissors | admitted_core | 20 | -0.04952535 | 0.0969283 | 0.27367991 | 400/457 | 拒绝 |
| m1_m2_scissors | admitted_core | 60 | -0.0740199 | 0.08490923 | 0.26912457 | 360/457 | 拒绝 |
| m1_m2_scissors | value_quality_sleeve | 5 | -0.01308075 | 0.13792267 | 0.34061036 | 415/457 | 拒绝 |
| m1_m2_scissors | value_quality_sleeve | 20 | -0.02261175 | 0.22363379 | 0.44726758 | 400/457 | 拒绝 |
| m1_m2_scissors | value_quality_sleeve | 60 | -0.04909191 | 0.15469983 | 0.34061036 | 360/457 | 拒绝 |
| m2_yoy | admitted_core | 5 | 0.01141087 | 0.00135709 | 0.01628508 | 113/614 | 拒绝 |
| m2_yoy | admitted_core | 20 | 0.03623127 | 0.03005223 | 0.16027856 | 113/599 | 拒绝 |
| m2_yoy | admitted_core | 60 | 0.10750674 | 0.35203011 | 0.5120438 | 113/559 | 拒绝 |
| m2_yoy | value_quality_sleeve | 5 | 0.0192382 | 0.00096278 | 0.01540448 | 113/614 | 拒绝 |
| m2_yoy | value_quality_sleeve | 20 | 0.03377153 | 0.03608666 | 0.17321597 | 113/599 | 拒绝 |
| m2_yoy | value_quality_sleeve | 60 | 0.08085168 | 0.28929691 | 0.49432385 | 113/559 | 拒绝 |
| term_spread_10y_2y | admitted_core | 5 | -0.01428582 | 0.00728964 | 0.05831712 | 193/471 | 拒绝 |
| term_spread_10y_2y | admitted_core | 20 | 0.00134171 | 0.23777626 | 0.44764189 | 193/458 | 拒绝 |
| term_spread_10y_2y | admitted_core | 60 | -0.00653531 | 0.31051982 | 0.49432385 | 193/454 | 拒绝 |
| term_spread_10y_2y | value_quality_sleeve | 5 | -0.01706492 | 0.00047282 | 0.01134768 | 193/471 | 拒绝 |
| term_spread_10y_2y | value_quality_sleeve | 20 | -0.01519132 | 0.08921426 | 0.26912457 | 193/458 | 拒绝 |
| term_spread_10y_2y | value_quality_sleeve | 60 | -0.03235056 | 0.13055699 | 0.34061036 | 193/454 | 拒绝 |
| credit_spread_5y_low | admitted_core | 5 | -0.02850354 | 0.0048712 | 0.04676352 | 654/70 | 拒绝 |
| credit_spread_5y_low | admitted_core | 20 | -0.06908742 | 0.08970819 | 0.26912457 | 639/70 | 拒绝 |
| credit_spread_5y_low | admitted_core | 60 | -0.15858347 | 0.30913048 | 0.49432385 | 599/70 | 拒绝 |
| credit_spread_5y_low | value_quality_sleeve | 5 | 0.0066046 | 1.885e-05 | 0.0009048 | 654/70 | 拒绝 |
| credit_spread_5y_low | value_quality_sleeve | 20 | -0.01234576 | 0.01246643 | 0.08548409 | 639/70 | 拒绝 |
| credit_spread_5y_low | value_quality_sleeve | 60 | -0.08839697 | 0.18175093 | 0.37930629 | 599/70 | 拒绝 |
| equity_risk_premium | admitted_core | 5 | 0.0040382 | 0.79689989 | 0.92735396 | 376/370 | 拒绝 |
| equity_risk_premium | admitted_core | 20 | -0.02617278 | 0.49519556 | 0.66086541 | 376/370 | 拒绝 |
| equity_risk_premium | admitted_core | 60 | -0.02328882 | 0.85007446 | 0.92735396 | 376/331 | 拒绝 |
| equity_risk_premium | value_quality_sleeve | 5 | -0.00093088 | 0.96476211 | 0.96476211 | 376/370 | 拒绝 |
| equity_risk_premium | value_quality_sleeve | 20 | 0.00160602 | 0.92852093 | 0.94827669 | 376/370 | 拒绝 |
| equity_risk_premium | value_quality_sleeve | 60 | -0.00299843 | 0.91675037 | 0.94827669 | 376/331 | 拒绝 |
| equity_risk_premium_chg3m | admitted_core | 5 | 0.00053393 | 0.15611308 | 0.34061036 | 207/405 | 拒绝 |
| equity_risk_premium_chg3m | admitted_core | 20 | 0.00157042 | 0.29959076 | 0.49432385 | 192/405 | 拒绝 |
| equity_risk_premium_chg3m | admitted_core | 60 | -0.02799868 | 0.1480875 | 0.34061036 | 170/405 | 拒绝 |
| equity_risk_premium_chg3m | value_quality_sleeve | 5 | 0.01480555 | 0.49564906 | 0.66086541 | 207/405 | 拒绝 |
| equity_risk_premium_chg3m | value_quality_sleeve | 20 | 0.02408033 | 0.72803478 | 0.87364174 | 192/405 | 拒绝 |
| equity_risk_premium_chg3m | value_quality_sleeve | 60 | -0.00917354 | 0.24247269 | 0.44764189 | 170/405 | 拒绝 |

## 边界

- Coverage is not alpha.
- Macro release timing is represented by local effective-date fields.
- Conditional IC is not a portfolio return.
- No leverage and no production activation.
