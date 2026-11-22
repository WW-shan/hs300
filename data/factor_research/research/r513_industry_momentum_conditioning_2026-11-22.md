# R513 行业动量条件化 IC 审计

| 条件变量 | 股票袖 | 周期 | 高-低 IC | NW p | q | 高/低天数 | 门槛 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| industry_mom_20d | admitted_core | 5 | 0.00268182 | 0.85981683 | 0.95574437 | 320/315 | 拒绝 |
| industry_mom_20d | admitted_core | 20 | 0.01227159 | 0.69154867 | 0.94162036 | 320/301 | 拒绝 |
| industry_mom_20d | value_quality_sleeve | 5 | -0.00499718 | 0.70621527 | 0.94162036 | 320/315 | 拒绝 |
| industry_mom_20d | value_quality_sleeve | 20 | 4.038e-05 | 0.93144169 | 0.95574437 | 320/301 | 拒绝 |
| industry_rps_20d | admitted_core | 5 | 0.00397376 | 0.32974593 | 0.94162036 | 469/352 | 拒绝 |
| industry_rps_20d | admitted_core | 20 | 0.01897474 | 0.25736436 | 0.94162036 | 469/337 | 拒绝 |
| industry_rps_20d | value_quality_sleeve | 5 | -0.00772521 | 0.95574437 | 0.95574437 | 469/352 | 拒绝 |
| industry_rps_20d | value_quality_sleeve | 20 | -0.00424986 | 0.65308171 | 0.94162036 | 469/337 | 拒绝 |
| industry_breadth_20d | admitted_core | 5 | -0.00561676 | 0.54288959 | 0.94162036 | 292/340 | 拒绝 |
| industry_breadth_20d | admitted_core | 20 | -0.01013669 | 0.6517587 | 0.94162036 | 292/330 | 拒绝 |
| industry_breadth_20d | value_quality_sleeve | 5 | -0.00952071 | 0.22664707 | 0.94162036 | 292/340 | 拒绝 |
| industry_breadth_20d | value_quality_sleeve | 20 | -0.00942016 | 0.57116184 | 0.94162036 | 292/330 | 拒绝 |

## 边界

- Industry momentum is a stock-level proxy from a current industry snapshot, not a historical industry index.
- Conditional IC is not a portfolio return.
- The test/recent windows have been observed before.
- No leverage and no production activation.
