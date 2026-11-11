# P3 扩展数据窗口参数族蒙特卡洛报告

- Research ID：`sha256:f75077fee50b760462f40c77e430d9d44123c96eb5fa456b9e6ae2ddde965eb1`
- 生成时间：`2026-11-11T11:12:50Z`
- 方法版本：`extended_parameter_mc_v1_conditioner_scoped`
- 样本：`2021-06-01` 至 `2026-11-05`
- 参数族：23；参数点：107
- 数值对齐：`pass`；变体数：25
- 市场级因子只生成 conditioner 研究候选，不进入股票 alpha 腿、watch 或 active 版本。

## 家族结论

| 参数族 | 角色 | 当前 | 推荐 | 结论 | 当前全样本 | 当前检验 | 门禁中性化 | q | 决赛主 CI |
| --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |
| `industry_momentum` | stock_alpha_research | 20 | - | rejected | -0.005449 | 0.009334 | size | 0.832919 | - |
| `industry_rps` | stock_alpha_research | 20 | - | rejected | -0.004126 | 0.011925 | size | 0.904308 | - |
| `industry_breadth` | stock_alpha_research | 20 | - | rejected | 0.006970 | 0.011720 | size | 0.615931 | - |
| `stock_rel_industry_strength` | stock_alpha_research | 20 | - | rejected | -0.010611 | -0.013843 | industry+size | 0.096495 | - |
| `insider_buy_event` | stock_alpha_research | 20 | - | rejected | - | - | industry+size | - | - |
| `insider_net_buy_event` | stock_alpha_research | 20 | - | rejected | - | - | industry+size | - | - |
| `insider_sell_event_low` | stock_alpha_research | 20 | - | rejected | - | - | industry+size | - | - |
| `lhb_net_buy_event` | stock_alpha_research | 20 | - | rejected | 0.008630 | -0.003349 | industry+size | 0.810034 | - |
| `lhb_inst_net_buy_event` | stock_alpha_research | 20 | - | rejected | 0.002597 | 0.003402 | industry+size | 0.953072 | - |
| `lhb_event_count_low` | stock_alpha_research | 20 | - | rejected | -0.000471 | 0.008328 | industry+size | 0.941454 | - |
| `repurchase_event_decay` | stock_alpha_research | 60 | 20 | recommended | 0.013987 | 0.011727 | industry+size | 0.085073 | 0.002168 |
| `block_trade_ratio_event` | stock_alpha_research | 20 | - | rejected | 0.000022 | 0.003057 | industry+size | 0.996885 | - |
| `block_premium_event` | stock_alpha_research | 20 | - | rejected | -0.016097 | -0.021299 | industry+size | 0.010290 | - |
| `analyst_rating_level` | stock_alpha_research | 20 | - | rejected | 0.001188 | 0.000117 | industry+size | 0.990102 | - |
| `analyst_rating_change` | stock_alpha_research | 20 | - | rejected | -0.006534 | 0.000157 | industry+size | 0.775327 | - |
| `bond_futures_momentum` | conditioner | 20 | - | rejected | -0.059572 | -0.003853 | none | 0.760562 | - |
| `bond_futures_trend_momentum` | conditioner | 20 | - | rejected | -0.039829 | 0.004860 | none | 0.879501 | - |
| `hsi_momentum` | conditioner | 20 | - | rejected | 0.028945 | 0.008758 | none | 0.776171 | - |
| `hscei_momentum` | conditioner | 20 | - | rejected | 0.025066 | -0.009734 | none | 0.821420 | - |
| `hsi_relative_hs300` | conditioner | 20 | - | rejected | 0.029182 | -0.004580 | none | 0.686573 | - |
| `etf_share_change` | conditioner | 1 | - | rejected | 0.027887 | 0.087709 | none | 0.892561 | - |
| `qvix_zscore` | conditioner | 252 | - | rejected | -0.130307 | 0.001038 | none | 0.008210 | - |
| `market_skew` | conditioner | 60 | - | rejected | 0.046353 | 0.109796 | none | 0.601556 | - |

## 参数点统计

| 参数族 | 参数 | 全样本 | 训练 | 检验 | 近一年 | 初筛 CI 下限 | q | 平台 | 决赛主 CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `industry_momentum` | 10 | -0.001960 | -0.002852 | -0.000187 | 0.005043 | -0.023194 | 0.832919 | not_robust | - |
| `industry_momentum` | 20 | -0.005449 | -0.012892 | 0.009334 | 0.009939 | -0.029376 | 0.832919 | not_robust | - |
| `industry_momentum` | 40 | -0.017843 | -0.029675 | 0.005658 | -0.009952 | -0.044870 | 0.550066 | not_robust | - |
| `industry_momentum` | 60 | -0.002565 | -0.015687 | 0.023501 | 0.026820 | -0.031263 | 0.832919 | not_robust | - |
| `industry_momentum` | 120 | -0.011203 | -0.021555 | 0.009361 | 0.008183 | -0.035790 | 0.812300 | not_robust | - |
| `industry_rps` | 10 | -0.001116 | -0.002790 | 0.002207 | 0.007145 | -0.022270 | 0.904308 | not_robust | - |
| `industry_rps` | 20 | -0.004126 | -0.012207 | 0.011925 | 0.014132 | -0.029572 | 0.904308 | not_robust | - |
| `industry_rps` | 40 | -0.017726 | -0.029867 | 0.006392 | -0.007159 | -0.043327 | 0.449782 | not_robust | - |
| `industry_rps` | 60 | -0.002548 | -0.015958 | 0.024089 | 0.029146 | -0.030702 | 0.904308 | not_robust | - |
| `industry_breadth` | 10 | 0.005907 | 0.009048 | -0.000311 | 0.004783 | -0.010238 | 0.615931 | not_robust | - |
| `industry_breadth` | 20 | 0.006970 | 0.004574 | 0.011720 | 0.009958 | -0.011543 | 0.615931 | not_robust | - |
| `industry_breadth` | 40 | -0.000403 | -0.009829 | 0.018321 | 0.017766 | -0.019601 | 0.965209 | not_robust | - |
| `industry_breadth` | 60 | -0.008010 | -0.018476 | 0.012778 | 0.010500 | -0.030003 | 0.615931 | not_robust | - |
| `stock_rel_industry_strength` | 10 | -0.008434 | -0.011713 | -0.001920 | -0.004572 | -0.020826 | 0.132091 | not_robust | - |
| `stock_rel_industry_strength` | 20 | -0.010611 | -0.008984 | -0.013843 | -0.016642 | -0.023337 | 0.096495 | not_robust | - |
| `stock_rel_industry_strength` | 40 | -0.013180 | -0.012469 | -0.014591 | -0.008360 | -0.026252 | 0.096495 | not_robust | - |
| `stock_rel_industry_strength` | 60 | -0.012140 | -0.012582 | -0.011262 | -0.005105 | -0.026981 | 0.096495 | not_robust | - |
| `insider_buy_event` | 5 | - | - | - | - | - | - | not_robust | - |
| `insider_buy_event` | 10 | - | - | - | - | - | - | not_robust | - |
| `insider_buy_event` | 20 | - | - | - | - | - | - | not_robust | - |
| `insider_buy_event` | 40 | - | - | - | - | - | - | not_robust | - |
| `insider_buy_event` | 60 | - | - | - | - | - | - | not_robust | - |
| `insider_net_buy_event` | 5 | - | - | - | - | - | - | not_robust | - |
| `insider_net_buy_event` | 10 | - | - | - | - | - | - | not_robust | - |
| `insider_net_buy_event` | 20 | - | - | - | - | - | - | not_robust | - |
| `insider_net_buy_event` | 40 | -0.083665 | -0.083665 | - | - | -0.083665 | 0.122155 | not_robust | - |
| `insider_net_buy_event` | 60 | -0.029760 | -0.027936 | -0.040895 | -0.040895 | -0.053581 | 0.066615 | not_robust | - |
| `insider_sell_event_low` | 5 | - | - | - | - | - | - | not_robust | - |
| `insider_sell_event_low` | 10 | - | - | - | - | - | - | not_robust | - |
| `insider_sell_event_low` | 20 | - | - | - | - | - | - | not_robust | - |
| `insider_sell_event_low` | 40 | - | - | - | - | - | - | not_robust | - |
| `insider_sell_event_low` | 60 | - | - | - | - | - | - | not_robust | - |
| `lhb_net_buy_event` | 5 | -0.004440 | -0.005242 | -0.002856 | -0.003379 | -0.019167 | 0.859323 | not_robust | - |
| `lhb_net_buy_event` | 10 | 0.001063 | 0.003192 | -0.003132 | 0.008197 | -0.014517 | 0.881163 | not_robust | - |
| `lhb_net_buy_event` | 20 | 0.008630 | 0.014667 | -0.003349 | 0.005579 | -0.006192 | 0.810034 | not_robust | - |
| `lhb_net_buy_event` | 40 | 0.003744 | 0.006977 | -0.002672 | 0.000542 | -0.009369 | 0.859323 | not_robust | - |
| `lhb_net_buy_event` | 60 | 0.000810 | 0.007242 | -0.011951 | -0.016066 | -0.012996 | 0.881163 | not_robust | - |
| `lhb_inst_net_buy_event` | 5 | -0.004530 | -0.000336 | -0.012595 | -0.043123 | -0.016852 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 10 | -0.001674 | 0.000648 | -0.006164 | -0.020144 | -0.012122 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 20 | 0.002597 | 0.002190 | 0.003402 | -0.006716 | -0.009979 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 40 | 0.000320 | -0.002847 | 0.006598 | 0.004079 | -0.012413 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 60 | -0.002645 | -0.002162 | -0.003603 | -0.002549 | -0.014705 | 0.953072 | not_robust | - |
| `lhb_event_count_low` | 5 | 0.005048 | 0.001337 | 0.012373 | -0.003448 | -0.008832 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 10 | 0.002301 | -0.006704 | 0.020045 | 0.007511 | -0.010884 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 20 | -0.000471 | -0.004906 | 0.008328 | -0.003953 | -0.013973 | 0.941454 | not_robust | - |
| `lhb_event_count_low` | 40 | 0.001813 | -0.000480 | 0.006363 | -0.003930 | -0.008104 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 60 | 0.004338 | 0.003188 | 0.006618 | 0.005164 | -0.005865 | 0.920241 | not_robust | - |
| `repurchase_event_decay` | 20 | 0.017762 | 0.017513 | 0.018234 | 0.000191 | 0.002009 | 0.061091 | robust_plateau | 0.002168 |
| `repurchase_event_decay` | 40 | 0.012302 | 0.010926 | 0.015010 | 0.008087 | -0.003968 | 0.085073 | robust_plateau | - |
| `repurchase_event_decay` | 60 | 0.013987 | 0.015125 | 0.011727 | 0.002133 | -0.001240 | 0.085073 | robust_plateau | - |
| `repurchase_event_decay` | 120 | 0.010378 | 0.008299 | 0.014508 | 0.012605 | -0.001107 | 0.085073 | robust_plateau | - |
| `block_trade_ratio_event` | 5 | -0.001547 | -0.005823 | 0.001616 | 0.001581 | -0.014214 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 10 | -0.004429 | -0.008573 | -0.001398 | 0.001426 | -0.016574 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 20 | 0.000022 | -0.004130 | 0.003057 | 0.002619 | -0.012405 | 0.996885 | not_robust | - |
| `block_trade_ratio_event` | 40 | 0.002375 | 0.000646 | 0.003638 | 0.001851 | -0.008373 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 60 | 0.002497 | 0.001232 | 0.003422 | -0.000595 | -0.008958 | 0.961151 | not_robust | - |
| `block_premium_event` | 5 | -0.015716 | -0.018013 | -0.014001 | -0.022111 | -0.028910 | 0.026457 | not_robust | - |
| `block_premium_event` | 10 | -0.018906 | -0.015488 | -0.021405 | -0.031433 | -0.032437 | 0.010290 | not_robust | - |
| `block_premium_event` | 20 | -0.016097 | -0.008983 | -0.021299 | -0.030447 | -0.028105 | 0.010290 | not_robust | - |
| `block_premium_event` | 40 | -0.006984 | 0.004268 | -0.015211 | -0.016705 | -0.015338 | 0.138828 | not_robust | - |
| `block_premium_event` | 60 | -0.006631 | 0.000525 | -0.011863 | -0.008212 | -0.016333 | 0.138828 | not_robust | - |
| `analyst_rating_level` | 5 | 0.001141 | 0.000362 | 0.002682 | 0.000520 | -0.005876 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 10 | 0.000794 | -0.000141 | 0.002644 | 0.002596 | -0.005531 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 20 | 0.001188 | 0.001728 | 0.000117 | 0.003226 | -0.006120 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 40 | 0.000064 | -0.002638 | 0.005413 | 0.001454 | -0.006451 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 60 | 0.000039 | -0.004561 | 0.009145 | 0.004911 | -0.007086 | 0.990102 | not_robust | - |
| `analyst_rating_change` | 5 | -0.306758 | - | -0.306758 | - | - | - | not_robust | - |
| `analyst_rating_change` | 10 | -0.009690 | 0.036486 | -0.049709 | - | -0.006070 | 0.775327 | not_robust | - |
| `analyst_rating_change` | 20 | -0.006534 | -0.010860 | 0.000157 | 0.036342 | -0.046188 | 0.775327 | not_robust | - |
| `analyst_rating_change` | 40 | 0.015720 | 0.009111 | 0.033823 | 0.059001 | -0.003939 | 0.118704 | not_robust | - |
| `analyst_rating_change` | 60 | 0.014337 | 0.015141 | 0.012264 | 0.017745 | 0.001824 | 0.110032 | not_robust | - |
| `bond_futures_momentum` | 5 | -0.025964 | -0.092015 | 0.094331 | 0.080922 | -0.093657 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 10 | -0.016519 | -0.027839 | -0.021747 | -0.092749 | -0.083258 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 20 | -0.059572 | -0.098722 | -0.003853 | 0.038420 | -0.146854 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 40 | 0.033758 | -0.006267 | 0.113871 | 0.262122 | -0.053267 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 60 | 0.015424 | -0.015667 | 0.027328 | 0.041288 | -0.063390 | 0.760562 | not_robust | - |
| `bond_futures_trend_momentum` | 5 | -0.009517 | -0.063043 | 0.087304 | 0.043975 | -0.075890 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 10 | -0.006677 | -0.013692 | -0.025532 | -0.119386 | -0.070578 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 20 | -0.039829 | -0.077271 | 0.004860 | 0.035500 | -0.130709 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 40 | 0.022890 | -0.000756 | 0.037607 | 0.087954 | -0.064527 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 60 | 0.026636 | -0.002182 | 0.003833 | -0.065948 | -0.055835 | 0.879501 | not_robust | - |
| `hsi_momentum` | 5 | 0.035521 | 0.040667 | -0.028812 | -0.093449 | -0.048216 | 0.776171 | not_robust | - |
| `hsi_momentum` | 10 | 0.020329 | 0.012720 | -0.022323 | -0.082173 | -0.060301 | 0.776171 | not_robust | - |
| `hsi_momentum` | 20 | 0.028945 | 0.004019 | 0.008758 | 0.049418 | -0.054049 | 0.776171 | not_robust | - |
| `hsi_momentum` | 40 | 0.014013 | -0.015349 | -0.032645 | -0.069814 | -0.080295 | 0.776171 | not_robust | - |
| `hsi_momentum` | 60 | 0.028363 | -0.014770 | -0.048144 | -0.219430 | -0.066941 | 0.776171 | not_robust | - |
| `hscei_momentum` | 5 | 0.017662 | 0.025817 | -0.051957 | -0.142259 | -0.059231 | 0.821420 | not_robust | - |
| `hscei_momentum` | 10 | 0.010650 | 0.017440 | -0.060960 | -0.136760 | -0.066011 | 0.821420 | not_robust | - |
| `hscei_momentum` | 20 | 0.025066 | 0.011633 | -0.009734 | 0.015732 | -0.067652 | 0.821420 | not_robust | - |
| `hscei_momentum` | 40 | 0.013352 | -0.008695 | -0.029858 | -0.097625 | -0.082402 | 0.821420 | not_robust | - |
| `hscei_momentum` | 60 | 0.012070 | -0.023307 | -0.051120 | -0.265367 | -0.071471 | 0.821420 | not_robust | - |
| `hsi_relative_hs300` | 5 | 0.089807 | 0.130520 | -0.022599 | -0.034179 | 0.004818 | 0.218190 | not_robust | - |
| `hsi_relative_hs300` | 10 | 0.081378 | 0.137401 | -0.055092 | -0.069530 | -0.010335 | 0.242751 | not_robust | - |
| `hsi_relative_hs300` | 20 | 0.029182 | 0.040072 | -0.004580 | 0.052184 | -0.060829 | 0.686573 | not_robust | - |
| `hsi_relative_hs300` | 40 | 0.012896 | 0.040443 | -0.071689 | -0.081571 | -0.094270 | 0.794026 | not_robust | - |
| `hsi_relative_hs300` | 60 | 0.044795 | 0.060230 | -0.024502 | -0.059578 | -0.049740 | 0.607087 | not_robust | - |
| `etf_share_change` | 1 | 0.027887 | 0.047094 | 0.087709 | 0.068339 | -0.068529 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 2 | 0.048871 | 0.137187 | 0.013730 | 0.041972 | -0.055716 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 3 | 0.010064 | 0.075216 | 0.018570 | -0.102798 | -0.087460 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 6 | 0.006810 | 0.042945 | 0.163638 | 0.109224 | -0.116753 | 0.892561 | robust_plateau | - |
| `qvix_zscore` | 60 | -0.132676 | -0.132541 | -0.119553 | -0.035979 | -0.238580 | 0.008210 | not_robust | - |
| `qvix_zscore` | 120 | -0.135713 | -0.143067 | -0.076081 | -0.047126 | -0.237243 | 0.008210 | not_robust | - |
| `qvix_zscore` | 252 | -0.130307 | -0.132845 | 0.001038 | -0.021386 | -0.232612 | 0.008210 | not_robust | - |
| `market_skew` | 20 | 0.043955 | 0.017604 | 0.061790 | -0.129727 | -0.062693 | 0.601556 | not_robust | - |
| `market_skew` | 40 | 0.002557 | -0.102715 | 0.055328 | -0.174375 | -0.086221 | 0.962972 | not_robust | - |
| `market_skew` | 60 | 0.046353 | -0.051647 | 0.109796 | -0.032507 | -0.033351 | 0.601556 | not_robust | - |
| `market_skew` | 120 | 0.038902 | -0.056343 | 0.160042 | -0.085624 | -0.043419 | 0.601556 | not_robust | - |

## 判定口径

- 股票/事件族：按注册表声明的行业、规模中性化执行 Rank IC 门禁；初筛要求稳健平台、全样本/训练/检验方向 IC 为正、门禁中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 bootstrap `ci_low > 0`。
- 市场 conditioner：使用时间序列 rank regression 的方向 beta；初筛要求稳健平台、全样本/训练/检验 beta 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 score bootstrap `ci_low > 0`。
- 决赛：入围点使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。
- 股票族推荐排序：先最大化 `min(train_ic, test_ic)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。
- 市场族推荐排序：先最大化 `min(train_beta, test_beta)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。
- 市场结论只允许用于 regime conditioner 研究，不能写成股票 alpha、watch 或 active。
