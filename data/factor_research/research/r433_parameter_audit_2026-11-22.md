# R433 参数族统一审计

- 注册参数族：62
- MC 覆盖：62
- 缺失族：[]
- 推荐参数：11
- 拒绝族：51
- 孤儿推荐（有推荐但不在当前 8 腿）：9

## 当前 8 腿中可调参数族

| 参数族 | 当前参数 | 推荐参数 | 当前集合成员 | 结论 |
| --- | ---: | ---: | --- | --- |
| idio_vol | 60 | 20 | ext_alt_hs300_idio_vol_40d_low | recommended |
| vol_of_vol | 60 | 20 | ext_alt_hs300_vol_of_vol_20d_low | recommended |

## 孤儿推荐

- cs_spread -> 60（成员：ext_alt_hs300_cs_spread_60d）
- annual_volatility -> 10（成员：annual_vol_20d）
- atr_risk -> 5（成员：atr_pct）
- bollinger_width -> 10（成员：boll_width）
- amihud -> 120（成员：amihud_20d）
- amount_level -> 5（成员：amount_mean_20d）
- turnover_level -> 5（成员：turnover_mean_20d）
- max_return -> 60（成员：max_ret_20d）
- repurchase_event_decay -> 20（成员：ext_alt_hs300_repurchase_event_60d）

## 边界

- 这是参数族审计，不是新的回测。
- 推荐参数是族内稳健候选，不等于组合最优。
- 不生成订单、纸面目标或生产配置。
