# 因子库研究清单

- catalog：`sha256:3e5968c1a056998ce53eacfbb0923a70e10f7d572d9955add877538d99c5bcad`
- 因子总数：235
- 已有研究证据：118
- 增量统计通过：11

## 维度分布

| 维度 | 数量 |
| --- | ---: |
| financial | 50 |
| flow | 36 |
| industry | 10 |
| macro | 20 |
| price | 109 |
| sentiment | 10 |

## 增量统计通过

| 因子 | 维度 | 方向 | 全样本IC | 近一年IC | 边际IC | q | 最大相关 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `ext_alt_hs300_dividend_yield` | financial | high | 0.032937 | 0.045456 | 0.0208 | 0.0317 | 0.045 |
| `inventory_turnover_improvement_yoy` | financial | high | - | - | 0.0099 | 0.0196 | 0.061 |
| `ext_alt_hs300_fund_holding_ratio_low` | flow | high | 0.010835 | 0.00214 | 0.0121 | 0.0317 | 0.058 |
| `ext_alt_hs300_holder_top10_share_chg_qoq` | flow | high | 0.002574 | 0.011456 | 0.0074 | 0.0317 | 0.007 |
| `ext_alt_hs300_holder_top10_share_chg_yoy` | flow | high | 0.00492 | 0.000907 | 0.0073 | 0.0234 | 0.003 |
| `ext_alt_hs300_lhb_event_count_20d_low` | flow | high | -0.00085 | -0.006544 | 0.0090 | 0.0234 | 0.062 |
| `ext_alt_hs300_lhb_inst_net_buy_ratio_20d` | flow | high | 0.00176 | -0.009586 | 0.0034 | 0.0781 | 0.017 |
| `ext_alt_hs300_short_ratio_low` | flow | high | 0.008927 | -0.006648 | 0.0134 | 0.0317 | 0.066 |
| `amihud_20d` | price | none | - | - | 0.0109 | 0.0317 | 0.016 |
| `ext_alt_hs300_idio_vol_40d_low` | price | high | 0.026022 | 0.035091 | 0.0245 | 0.0098 | 0.168 |
| `ext_alt_hs300_vol_of_vol_60d_low` | price | high | 0.015967 | 0.044244 | 0.0153 | 0.0789 | 0.061 |

## 说明

- 这个清单是研究索引，不是 active 因子集。
- 缺失 IC/增量证据显示为 `null`，不代表 0。
- 参数族信息来自 `parameters/families.json`；新增因子应走同一套审计、增量和组合验证。
