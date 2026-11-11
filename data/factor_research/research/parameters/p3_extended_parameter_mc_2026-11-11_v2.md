# P3 扩展数据窗口参数族蒙特卡洛报告

- Research ID：`sha256:134c63bb03417d7fdbb47468a32acbca91e233fc04e60a7c422e2b337703226f`
- 生成时间：`2026-11-11T11:18:40Z`
- 方法版本：`extended_parameter_mc_v2_sparse_zero_filled`
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
| `insider_buy_event` | stock_alpha_research | 20 | - | rejected | -0.009124 | 0.009681 | industry+size | 0.192198 | - |
| `insider_net_buy_event` | stock_alpha_research | 20 | - | rejected | -0.004571 | 0.015670 | industry+size | 0.533335 | - |
| `insider_sell_event_low` | stock_alpha_research | 20 | - | rejected | 0.008718 | 0.007785 | industry+size | 0.537757 | - |
| `lhb_net_buy_event` | stock_alpha_research | 20 | - | rejected | 0.008630 | -0.003349 | industry+size | 0.810034 | - |
| `lhb_inst_net_buy_event` | stock_alpha_research | 20 | - | rejected | 0.002597 | 0.003402 | industry+size | 0.953072 | - |
| `lhb_event_count_low` | stock_alpha_research | 20 | - | rejected | -0.000471 | 0.008328 | industry+size | 0.941454 | - |
| `repurchase_event_decay` | stock_alpha_research | 60 | 20 | recommended | 0.013987 | 0.011727 | industry+size | 0.085073 | 0.001781 |
| `block_trade_ratio_event` | stock_alpha_research | 20 | - | rejected | 0.000022 | 0.003057 | industry+size | 0.996885 | - |
| `block_premium_event` | stock_alpha_research | 20 | - | rejected | -0.016097 | -0.021299 | industry+size | 0.010290 | - |
| `analyst_rating_level` | stock_alpha_research | 20 | - | rejected | 0.001188 | 0.000117 | industry+size | 0.990102 | - |
| `analyst_rating_change` | stock_alpha_research | 20 | - | rejected | -0.003528 | -0.003863 | industry+size | 0.714106 | - |
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
| `industry_momentum` | 10 | -0.001960 | -0.002852 | -0.000187 | 0.005043 | -0.022428 | 0.832919 | not_robust | - |
| `industry_momentum` | 20 | -0.005449 | -0.012892 | 0.009334 | 0.009939 | -0.030480 | 0.832919 | not_robust | - |
| `industry_momentum` | 40 | -0.017843 | -0.029675 | 0.005658 | -0.009952 | -0.042649 | 0.550066 | not_robust | - |
| `industry_momentum` | 60 | -0.002565 | -0.015687 | 0.023501 | 0.026820 | -0.029938 | 0.832919 | not_robust | - |
| `industry_momentum` | 120 | -0.011203 | -0.021555 | 0.009361 | 0.008183 | -0.035227 | 0.812300 | not_robust | - |
| `industry_rps` | 10 | -0.001116 | -0.002790 | 0.002207 | 0.007145 | -0.021467 | 0.904308 | not_robust | - |
| `industry_rps` | 20 | -0.004126 | -0.012207 | 0.011925 | 0.014132 | -0.028213 | 0.904308 | not_robust | - |
| `industry_rps` | 40 | -0.017726 | -0.029867 | 0.006392 | -0.007159 | -0.042997 | 0.449782 | not_robust | - |
| `industry_rps` | 60 | -0.002548 | -0.015958 | 0.024089 | 0.029146 | -0.028710 | 0.904308 | not_robust | - |
| `industry_breadth` | 10 | 0.005907 | 0.009048 | -0.000311 | 0.004783 | -0.010027 | 0.615931 | not_robust | - |
| `industry_breadth` | 20 | 0.006970 | 0.004574 | 0.011720 | 0.009958 | -0.011585 | 0.615931 | not_robust | - |
| `industry_breadth` | 40 | -0.000403 | -0.009829 | 0.018321 | 0.017766 | -0.021839 | 0.965209 | not_robust | - |
| `industry_breadth` | 60 | -0.008010 | -0.018476 | 0.012778 | 0.010500 | -0.028928 | 0.615931 | not_robust | - |
| `stock_rel_industry_strength` | 10 | -0.008434 | -0.011713 | -0.001920 | -0.004572 | -0.021005 | 0.132091 | not_robust | - |
| `stock_rel_industry_strength` | 20 | -0.010611 | -0.008984 | -0.013843 | -0.016642 | -0.023144 | 0.096495 | not_robust | - |
| `stock_rel_industry_strength` | 40 | -0.013180 | -0.012469 | -0.014591 | -0.008360 | -0.027304 | 0.096495 | not_robust | - |
| `stock_rel_industry_strength` | 60 | -0.012140 | -0.012582 | -0.011262 | -0.005105 | -0.027567 | 0.096495 | not_robust | - |
| `insider_buy_event` | 5 | -0.019157 | -0.024355 | -0.007957 | 0.004818 | -0.036145 | 0.032353 | not_robust | - |
| `insider_buy_event` | 10 | -0.013379 | -0.018280 | -0.003637 | -0.008129 | -0.029849 | 0.110876 | not_robust | - |
| `insider_buy_event` | 20 | -0.009124 | -0.018701 | 0.009681 | 0.011119 | -0.022323 | 0.192198 | not_robust | - |
| `insider_buy_event` | 40 | -0.005883 | -0.014115 | 0.010469 | 0.016551 | -0.017636 | 0.310084 | not_robust | - |
| `insider_buy_event` | 60 | -0.008353 | -0.011389 | -0.002323 | -0.009979 | -0.020523 | 0.182454 | not_robust | - |
| `insider_net_buy_event` | 5 | -0.008456 | -0.020507 | 0.015819 | 0.001277 | -0.026109 | 0.533335 | not_robust | - |
| `insider_net_buy_event` | 10 | -0.010082 | -0.022716 | 0.014840 | 0.002462 | -0.029401 | 0.533335 | not_robust | - |
| `insider_net_buy_event` | 20 | -0.004571 | -0.014761 | 0.015670 | 0.004574 | -0.019763 | 0.533335 | not_robust | - |
| `insider_net_buy_event` | 40 | -0.005567 | -0.011368 | 0.005956 | 0.003271 | -0.018354 | 0.533335 | not_robust | - |
| `insider_net_buy_event` | 60 | -0.003619 | -0.008793 | 0.006659 | 0.001539 | -0.015133 | 0.533335 | not_robust | - |
| `insider_sell_event_low` | 5 | 0.005717 | -0.000752 | 0.018567 | -0.001549 | -0.012411 | 0.669689 | not_robust | - |
| `insider_sell_event_low` | 10 | 0.008241 | 0.004184 | 0.016256 | 0.005527 | -0.010048 | 0.537757 | not_robust | - |
| `insider_sell_event_low` | 20 | 0.008718 | 0.009194 | 0.007785 | -0.003295 | -0.004903 | 0.537757 | not_robust | - |
| `insider_sell_event_low` | 40 | -0.000468 | -0.003673 | 0.005898 | -0.008510 | -0.013836 | 0.944879 | not_robust | - |
| `insider_sell_event_low` | 60 | 0.006259 | 0.004787 | 0.009183 | 0.001616 | -0.006886 | 0.537757 | not_robust | - |
| `lhb_net_buy_event` | 5 | -0.004440 | -0.005242 | -0.002856 | -0.003379 | -0.018725 | 0.859323 | not_robust | - |
| `lhb_net_buy_event` | 10 | 0.001063 | 0.003192 | -0.003132 | 0.008197 | -0.014053 | 0.881163 | not_robust | - |
| `lhb_net_buy_event` | 20 | 0.008630 | 0.014667 | -0.003349 | 0.005579 | -0.005420 | 0.810034 | not_robust | - |
| `lhb_net_buy_event` | 40 | 0.003744 | 0.006977 | -0.002672 | 0.000542 | -0.010171 | 0.859323 | not_robust | - |
| `lhb_net_buy_event` | 60 | 0.000810 | 0.007242 | -0.011951 | -0.016066 | -0.013623 | 0.881163 | not_robust | - |
| `lhb_inst_net_buy_event` | 5 | -0.004530 | -0.000336 | -0.012595 | -0.043123 | -0.017152 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 10 | -0.001674 | 0.000648 | -0.006164 | -0.020144 | -0.012408 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 20 | 0.002597 | 0.002190 | 0.003402 | -0.006716 | -0.010581 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 40 | 0.000320 | -0.002847 | 0.006598 | 0.004079 | -0.011725 | 0.953072 | not_robust | - |
| `lhb_inst_net_buy_event` | 60 | -0.002645 | -0.002162 | -0.003603 | -0.002549 | -0.015633 | 0.953072 | not_robust | - |
| `lhb_event_count_low` | 5 | 0.005048 | 0.001337 | 0.012373 | -0.003448 | -0.008234 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 10 | 0.002301 | -0.006704 | 0.020045 | 0.007511 | -0.012055 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 20 | -0.000471 | -0.004906 | 0.008328 | -0.003953 | -0.013763 | 0.941454 | not_robust | - |
| `lhb_event_count_low` | 40 | 0.001813 | -0.000480 | 0.006363 | -0.003930 | -0.008641 | 0.920241 | not_robust | - |
| `lhb_event_count_low` | 60 | 0.004338 | 0.003188 | 0.006618 | 0.005164 | -0.004955 | 0.920241 | not_robust | - |
| `repurchase_event_decay` | 20 | 0.017762 | 0.017513 | 0.018234 | 0.000191 | 0.000726 | 0.061091 | robust_plateau | 0.001781 |
| `repurchase_event_decay` | 40 | 0.012302 | 0.010926 | 0.015010 | 0.008087 | -0.002804 | 0.085073 | robust_plateau | - |
| `repurchase_event_decay` | 60 | 0.013987 | 0.015125 | 0.011727 | 0.002133 | -0.001523 | 0.085073 | robust_plateau | - |
| `repurchase_event_decay` | 120 | 0.010378 | 0.008299 | 0.014508 | 0.012605 | -0.000365 | 0.085073 | robust_plateau | - |
| `block_trade_ratio_event` | 5 | -0.001547 | -0.005823 | 0.001616 | 0.001581 | -0.014504 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 10 | -0.004429 | -0.008573 | -0.001398 | 0.001426 | -0.016616 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 20 | 0.000022 | -0.004130 | 0.003057 | 0.002619 | -0.012149 | 0.996885 | not_robust | - |
| `block_trade_ratio_event` | 40 | 0.002375 | 0.000646 | 0.003638 | 0.001851 | -0.008293 | 0.961151 | not_robust | - |
| `block_trade_ratio_event` | 60 | 0.002497 | 0.001232 | 0.003422 | -0.000595 | -0.008727 | 0.961151 | not_robust | - |
| `block_premium_event` | 5 | -0.015716 | -0.018013 | -0.014001 | -0.022111 | -0.028641 | 0.026457 | not_robust | - |
| `block_premium_event` | 10 | -0.018906 | -0.015488 | -0.021405 | -0.031433 | -0.033229 | 0.010290 | not_robust | - |
| `block_premium_event` | 20 | -0.016097 | -0.008983 | -0.021299 | -0.030447 | -0.028334 | 0.010290 | not_robust | - |
| `block_premium_event` | 40 | -0.006984 | 0.004268 | -0.015211 | -0.016705 | -0.016197 | 0.138828 | not_robust | - |
| `block_premium_event` | 60 | -0.006631 | 0.000525 | -0.011863 | -0.008212 | -0.016429 | 0.138828 | not_robust | - |
| `analyst_rating_level` | 5 | 0.001141 | 0.000362 | 0.002682 | 0.000520 | -0.005202 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 10 | 0.000794 | -0.000141 | 0.002644 | 0.002596 | -0.005322 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 20 | 0.001188 | 0.001728 | 0.000117 | 0.003226 | -0.006042 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 40 | 0.000064 | -0.002638 | 0.005413 | 0.001454 | -0.006414 | 0.990102 | not_robust | - |
| `analyst_rating_level` | 60 | 0.000039 | -0.004561 | 0.009145 | 0.004911 | -0.007345 | 0.990102 | not_robust | - |
| `analyst_rating_change` | 5 | -0.007084 | -0.004363 | -0.013146 | -0.012985 | -0.019476 | 0.488317 | not_robust | - |
| `analyst_rating_change` | 10 | -0.010664 | -0.010608 | -0.010778 | -0.028363 | -0.024371 | 0.340562 | not_robust | - |
| `analyst_rating_change` | 20 | -0.003528 | -0.003359 | -0.003863 | -0.010872 | -0.014869 | 0.714106 | not_robust | - |
| `analyst_rating_change` | 40 | -0.000287 | 0.002114 | -0.005039 | 0.001979 | -0.011400 | 0.955675 | not_robust | - |
| `analyst_rating_change` | 60 | -0.006995 | -0.002654 | -0.015590 | -0.019646 | -0.016261 | 0.340562 | not_robust | - |
| `bond_futures_momentum` | 5 | -0.025964 | -0.092015 | 0.094331 | 0.080922 | -0.100222 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 10 | -0.016519 | -0.027839 | -0.021747 | -0.092749 | -0.086670 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 20 | -0.059572 | -0.098722 | -0.003853 | 0.038420 | -0.145276 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 40 | 0.033758 | -0.006267 | 0.113871 | 0.262122 | -0.049741 | 0.760562 | not_robust | - |
| `bond_futures_momentum` | 60 | 0.015424 | -0.015667 | 0.027328 | 0.041288 | -0.065409 | 0.760562 | not_robust | - |
| `bond_futures_trend_momentum` | 5 | -0.009517 | -0.063043 | 0.087304 | 0.043975 | -0.079723 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 10 | -0.006677 | -0.013692 | -0.025532 | -0.119386 | -0.078516 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 20 | -0.039829 | -0.077271 | 0.004860 | 0.035500 | -0.122106 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 40 | 0.022890 | -0.000756 | 0.037607 | 0.087954 | -0.066382 | 0.879501 | not_robust | - |
| `bond_futures_trend_momentum` | 60 | 0.026636 | -0.002182 | 0.003833 | -0.065948 | -0.054287 | 0.879501 | not_robust | - |
| `hsi_momentum` | 5 | 0.035521 | 0.040667 | -0.028812 | -0.093449 | -0.042428 | 0.776171 | not_robust | - |
| `hsi_momentum` | 10 | 0.020329 | 0.012720 | -0.022323 | -0.082173 | -0.062805 | 0.776171 | not_robust | - |
| `hsi_momentum` | 20 | 0.028945 | 0.004019 | 0.008758 | 0.049418 | -0.056557 | 0.776171 | not_robust | - |
| `hsi_momentum` | 40 | 0.014013 | -0.015349 | -0.032645 | -0.069814 | -0.086552 | 0.776171 | not_robust | - |
| `hsi_momentum` | 60 | 0.028363 | -0.014770 | -0.048144 | -0.219430 | -0.064835 | 0.776171 | not_robust | - |
| `hscei_momentum` | 5 | 0.017662 | 0.025817 | -0.051957 | -0.142259 | -0.059038 | 0.821420 | not_robust | - |
| `hscei_momentum` | 10 | 0.010650 | 0.017440 | -0.060960 | -0.136760 | -0.065116 | 0.821420 | not_robust | - |
| `hscei_momentum` | 20 | 0.025066 | 0.011633 | -0.009734 | 0.015732 | -0.060848 | 0.821420 | not_robust | - |
| `hscei_momentum` | 40 | 0.013352 | -0.008695 | -0.029858 | -0.097625 | -0.090564 | 0.821420 | not_robust | - |
| `hscei_momentum` | 60 | 0.012070 | -0.023307 | -0.051120 | -0.265367 | -0.070013 | 0.821420 | not_robust | - |
| `hsi_relative_hs300` | 5 | 0.089807 | 0.130520 | -0.022599 | -0.034179 | 0.004158 | 0.218190 | not_robust | - |
| `hsi_relative_hs300` | 10 | 0.081378 | 0.137401 | -0.055092 | -0.069530 | -0.016932 | 0.242751 | not_robust | - |
| `hsi_relative_hs300` | 20 | 0.029182 | 0.040072 | -0.004580 | 0.052184 | -0.063841 | 0.686573 | not_robust | - |
| `hsi_relative_hs300` | 40 | 0.012896 | 0.040443 | -0.071689 | -0.081571 | -0.082632 | 0.794026 | not_robust | - |
| `hsi_relative_hs300` | 60 | 0.044795 | 0.060230 | -0.024502 | -0.059578 | -0.053833 | 0.607087 | not_robust | - |
| `etf_share_change` | 1 | 0.027887 | 0.047094 | 0.087709 | 0.068339 | -0.065016 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 2 | 0.048871 | 0.137187 | 0.013730 | 0.041972 | -0.042261 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 3 | 0.010064 | 0.075216 | 0.018570 | -0.102798 | -0.094133 | 0.892561 | robust_plateau | - |
| `etf_share_change` | 6 | 0.006810 | 0.042945 | 0.163638 | 0.109224 | -0.111363 | 0.892561 | robust_plateau | - |
| `qvix_zscore` | 60 | -0.132676 | -0.132541 | -0.119553 | -0.035979 | -0.246473 | 0.008210 | not_robust | - |
| `qvix_zscore` | 120 | -0.135713 | -0.143067 | -0.076081 | -0.047126 | -0.239215 | 0.008210 | not_robust | - |
| `qvix_zscore` | 252 | -0.130307 | -0.132845 | 0.001038 | -0.021386 | -0.233601 | 0.008210 | not_robust | - |
| `market_skew` | 20 | 0.043955 | 0.017604 | 0.061790 | -0.129727 | -0.056964 | 0.601556 | not_robust | - |
| `market_skew` | 40 | 0.002557 | -0.102715 | 0.055328 | -0.174375 | -0.094593 | 0.962972 | not_robust | - |
| `market_skew` | 60 | 0.046353 | -0.051647 | 0.109796 | -0.032507 | -0.030946 | 0.601556 | not_robust | - |
| `market_skew` | 120 | 0.038902 | -0.056343 | 0.160042 | -0.085624 | -0.048105 | 0.601556 | not_robust | - |

## 判定口径

- 股票/事件族：按注册表声明的行业、规模中性化执行 Rank IC 门禁；初筛要求稳健平台、全样本/训练/检验方向 IC 为正、门禁中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 bootstrap `ci_low > 0`。
- 市场 conditioner：使用时间序列 rank regression 的方向 beta；初筛要求稳健平台、全样本/训练/检验 beta 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 score bootstrap `ci_low > 0`。
- 决赛：入围点使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。
- 股票族推荐排序：先最大化 `min(train_ic, test_ic)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。
- 市场族推荐排序：先最大化 `min(train_beta, test_beta)`，再取主 block 决赛 `ci_low` 更高者，最后取更接近当前参数者。
- 市场结论只允许用于 regime conditioner 研究，不能写成股票 alpha、watch 或 active。
