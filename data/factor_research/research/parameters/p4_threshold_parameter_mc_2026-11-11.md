# P4 阈值与事件后持有期参数报告

- Research ID：`sha256:128842a1c50fe850fd961e4cc46cafd8a1cfe372f5f6bbcc81fbe3171f66c588`
- 生成时间：`2026-11-11T12:22:05Z`
- 方法版本：`threshold_parameter_mc_v1`
- 样本：`2021-06-01` 至 `2026-11-05`
- 参数族：23；参数点：190
- 阈值和持有期只生成股票研究候选，不修改注册因子，不自动进入 watch 或 active。

## 家族结论

| 参数族 | 类型 | 口径 | 源因子 | 当前 | 推荐 | 结论 | 当前全样本 | 当前检验 | q | 决赛主 CI |
| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `dividend_yield_absolute` | threshold | absolute | `ext_alt_hs300_dividend_yield` | 0.02 | 0.015 | recommended | 0.032147 | 0.023122 | 0.000041 | 0.014505 |
| `dividend_yield_quantile` | threshold | quantile | `ext_alt_hs300_dividend_yield` | 0.5 | 0.4 | recommended | 0.027018 | 0.019686 | 0.000250 | 0.007985 |
| `fund_count_absolute` | threshold | absolute | `ext_alt_hs300_fund_count_low` | -5.0 | - | rejected | -0.003642 | -0.007994 | 0.574102 | - |
| `fund_count_quantile` | threshold | quantile | `ext_alt_hs300_fund_count_low` | 0.5 | - | rejected | 0.000932 | -0.010697 | 0.816221 | - |
| `fund_count_change_absolute` | threshold | absolute | `ext_alt_hs300_fund_count_chg_qoq_low` | 0.2 | - | rejected | 0.003614 | 0.018108 | 0.916352 | - |
| `fund_count_change_quantile` | threshold | quantile | `ext_alt_hs300_fund_count_chg_qoq_low` | 0.5 | - | rejected | -0.003838 | -0.000498 | 0.624365 | - |
| `fund_holding_ratio_absolute` | threshold | absolute | `ext_alt_hs300_fund_holding_ratio_low` | -0.05 | - | rejected | 0.010481 | 0.004027 | 0.023491 | - |
| `fund_holding_ratio_quantile` | threshold | quantile | `ext_alt_hs300_fund_holding_ratio_low` | 0.5 | - | rejected | 0.010237 | 0.000157 | 0.063545 | - |
| `fund_holding_change_absolute` | threshold | absolute | `ext_alt_hs300_fund_holding_ratio_chg_qoq_low` | -0.5 | - | rejected | 0.004011 | 0.023829 | 0.645262 | - |
| `fund_holding_change_quantile` | threshold | quantile | `ext_alt_hs300_fund_holding_ratio_chg_qoq_low` | 0.5 | - | rejected | 0.003381 | -0.003260 | 0.821078 | - |
| `margin_change_absolute` | threshold | absolute | `ext_alt_hs300_margin_chg_1m_low` | 0.0 | - | rejected | 0.013151 | 0.019296 | 0.000089 | - |
| `margin_change_quantile` | threshold | quantile | `ext_alt_hs300_margin_chg_1m_low` | 0.5 | 0.5 | recommended | 0.008998 | 0.008519 | 0.025552 | 0.002451 |
| `holder_net_buy_ratio_absolute` | threshold | absolute | `ext_alt_hs300_holder_net_buy_ratio_90d` | 0.5 | - | rejected | -0.017315 | -0.015082 | 0.038886 | - |
| `holder_net_buy_ratio_quantile` | threshold | quantile | `ext_alt_hs300_holder_net_buy_ratio_90d` | 0.5 | - | rejected | -0.006410 | 0.002694 | 0.406833 | - |
| `holder_net_buy_count_absolute` | threshold | absolute | `ext_alt_hs300_holder_net_buy_count_90d` | 2.0 | - | rejected | -0.017076 | -0.001855 | 0.070598 | - |
| `holder_net_buy_count_quantile` | threshold | quantile | `ext_alt_hs300_holder_net_buy_count_90d` | 0.5 | - | rejected | -0.006736 | 0.007639 | 0.487933 | - |
| `pledge_change_absolute` | threshold | absolute | `ext_alt_hs300_pledge_ratio_chg_1m_low` | 0.2 | - | rejected | 0.014239 | 0.018414 | 0.250846 | - |
| `pledge_change_quantile` | threshold | quantile | `ext_alt_hs300_pledge_ratio_chg_1m_low` | 0.5 | - | rejected | 0.002474 | 0.007977 | 0.934183 | - |
| `insider_buy_holding` | event_holding | step | `ext_alt_hs300_insider_buy_20d` | 20.0 | - | rejected | -0.007501 | 0.009627 | 0.306034 | - |
| `lhb_net_buy_holding` | event_holding | step | `ext_alt_hs300_lhb_net_buy_ratio_20d` | 20.0 | - | rejected | 0.011321 | -0.002559 | 0.385671 | - |
| `repurchase_holding` | event_holding | step | `ext_alt_hs300_repurchase_event_60d` | 20.0 | 20.0 | recommended | 0.016842 | 0.018118 | 0.075915 | 0.000001 |
| `block_premium_holding` | event_holding | step | `ext_alt_hs300_block_premium_20d` | 20.0 | - | rejected | -0.014666 | -0.021752 | 0.008486 | - |
| `analyst_rating_change_holding` | event_holding | step | `ext_alt_hs300_analyst_rating_change_20d` | 20.0 | - | rejected | -0.003456 | -0.004696 | 0.730457 | - |

## 参数点统计

| 参数族 | 参数 | 通过比例 | 全样本 IC | 训练 IC | 检验 IC | 近一年 IC | 初筛 CI 下限 | q | 平台 | 决赛主 CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `dividend_yield_absolute` | 0 | 1.000000 | -0.093556 | -0.093556 | - | - | -0.093556 | - | not_robust | - |
| `dividend_yield_absolute` | 0.005 | 0.757327 | 0.010232 | 0.008516 | 0.013639 | 0.012225 | 0.001523 | 0.018869 | not_robust | - |
| `dividend_yield_absolute` | 0.01 | 0.609044 | 0.022699 | 0.023709 | 0.020694 | 0.029688 | 0.010165 | 0.000077 | robust_plateau | 0.010180 |
| `dividend_yield_absolute` | 0.015 | 0.489182 | 0.030251 | 0.033518 | 0.023763 | 0.042711 | 0.013767 | 0.000042 | robust_plateau | 0.014505 |
| `dividend_yield_absolute` | 0.02 | 0.394762 | 0.032147 | 0.036691 | 0.023122 | 0.044493 | 0.017113 | 0.000041 | robust_plateau | 0.014867 |
| `dividend_yield_absolute` | 0.03 | 0.247226 | 0.024886 | 0.029934 | 0.014859 | 0.032336 | 0.010366 | 0.000071 | robust_plateau | 0.010072 |
| `dividend_yield_absolute` | 0.04 | 0.160517 | 0.020571 | 0.023392 | 0.014967 | 0.022423 | 0.007584 | 0.000135 | robust_plateau | 0.007508 |
| `dividend_yield_absolute` | 0.05 | 0.096335 | 0.020020 | 0.021665 | 0.016754 | 0.001891 | 0.007087 | 0.000341 | robust_plateau | 0.007065 |
| `dividend_yield_absolute` | 0.06 | 0.050159 | 0.009520 | 0.011857 | 0.004880 | -0.019079 | -0.005575 | 0.159911 | robust_plateau | - |
| `dividend_yield_quantile` | 0.05 | 0.965325 | -0.004245 | -0.019432 | 0.018397 | 0.015319 | -0.023303 | 0.781071 | not_robust | - |
| `dividend_yield_quantile` | 0.1 | 0.911395 | -0.000273 | -0.013901 | 0.022482 | 0.020626 | -0.016489 | 0.969973 | not_robust | - |
| `dividend_yield_quantile` | 0.2 | 0.799172 | 0.006617 | 0.005026 | 0.009775 | 0.009506 | -0.003399 | 0.186573 | not_robust | - |
| `dividend_yield_quantile` | 0.3 | 0.699400 | 0.014750 | 0.013329 | 0.017574 | 0.024493 | 0.004565 | 0.003534 | robust_plateau | 0.004094 |
| `dividend_yield_quantile` | 0.4 | 0.599861 | 0.022480 | 0.022321 | 0.022796 | 0.039276 | 0.008483 | 0.000613 | robust_plateau | 0.007985 |
| `dividend_yield_quantile` | 0.5 | 0.500996 | 0.027018 | 0.030709 | 0.019686 | 0.040781 | 0.011004 | 0.000250 | robust_plateau | 0.011340 |
| `dividend_yield_quantile` | 0.6 | 0.400694 | 0.029633 | 0.034444 | 0.020077 | 0.039715 | 0.013124 | 0.000096 | robust_plateau | 0.014027 |
| `dividend_yield_quantile` | 0.7 | 0.300917 | 0.025171 | 0.031723 | 0.012157 | 0.028846 | 0.012420 | 0.000096 | robust_plateau | 0.010334 |
| `dividend_yield_quantile` | 0.8 | 0.201367 | 0.021293 | 0.026246 | 0.011454 | 0.017565 | 0.008041 | 0.000096 | robust_plateau | 0.008378 |
| `dividend_yield_quantile` | 0.9 | 0.101608 | 0.016706 | 0.018353 | 0.013433 | -0.003665 | 0.004987 | 0.001712 | not_robust | - |
| `dividend_yield_quantile` | 0.95 | 0.051827 | 0.000478 | -0.000974 | 0.003363 | -0.015044 | -0.015053 | 0.969973 | not_robust | - |
| `fund_count_absolute` | -8 | 0.999830 | -0.046115 | - | -0.046115 | -0.046115 | -0.072549 | 0.448035 | not_robust | - |
| `fund_count_absolute` | -7 | 0.964992 | -0.013044 | -0.020124 | 0.001019 | 0.005075 | -0.025371 | 0.101151 | not_robust | - |
| `fund_count_absolute` | -6 | 0.745568 | 0.002867 | 0.009295 | -0.009901 | -0.014264 | -0.005009 | 0.574102 | not_robust | - |
| `fund_count_absolute` | -5 | 0.467220 | -0.003642 | -0.001691 | -0.007994 | -0.010088 | -0.012893 | 0.574102 | not_robust | - |
| `fund_count_absolute` | -4 | 0.301958 | 0.003577 | 0.008330 | -0.008722 | -0.023165 | -0.006466 | 0.574102 | not_robust | - |
| `fund_count_absolute` | -3 | 0.168267 | -0.002107 | 0.004038 | -0.025187 | -0.026311 | -0.012395 | 0.650117 | not_robust | - |
| `fund_count_absolute` | -2 | 0.067954 | 0.004864 | 0.001030 | 0.016101 | 0.018746 | -0.010660 | 0.574102 | not_robust | - |
| `fund_count_quantile` | 0.05 | 0.949479 | -0.008056 | -0.004918 | -0.014289 | -0.020148 | -0.016252 | 0.185809 | not_robust | - |
| `fund_count_quantile` | 0.1 | 0.899442 | -0.002244 | 0.000545 | -0.007784 | -0.008209 | -0.011119 | 0.643526 | not_robust | - |
| `fund_count_quantile` | 0.2 | 0.800227 | 0.001645 | 0.007157 | -0.009305 | -0.007904 | -0.008086 | 0.761624 | not_robust | - |
| `fund_count_quantile` | 0.3 | 0.701405 | 0.006109 | 0.012346 | -0.006279 | -0.005956 | -0.003358 | 0.266169 | not_robust | - |
| `fund_count_quantile` | 0.4 | 0.601865 | 0.003609 | 0.010391 | -0.009863 | -0.006545 | -0.006276 | 0.610681 | not_robust | - |
| `fund_count_quantile` | 0.5 | 0.503158 | 0.000932 | 0.006787 | -0.010697 | -0.009563 | -0.007610 | 0.816221 | not_robust | - |
| `fund_count_quantile` | 0.6 | 0.403151 | -0.002304 | 0.005185 | -0.017180 | -0.019487 | -0.009460 | 0.643526 | not_robust | - |
| `fund_count_quantile` | 0.7 | 0.304616 | -0.007686 | -0.001486 | -0.020002 | -0.024432 | -0.015315 | 0.185809 | not_robust | - |
| `fund_count_quantile` | 0.8 | 0.206220 | -0.007539 | -0.004703 | -0.013172 | -0.008872 | -0.017404 | 0.205343 | not_robust | - |
| `fund_count_quantile` | 0.9 | 0.105037 | 0.008020 | 0.007186 | 0.009882 | 0.012176 | -0.004168 | 0.266169 | not_robust | - |
| `fund_count_quantile` | 0.95 | 0.056240 | 0.011534 | 0.011283 | 0.012095 | 0.001653 | -0.002565 | 0.185809 | not_robust | - |
| `fund_count_change_absolute` | -0.5 | 0.519780 | 0.010069 | 0.009248 | 0.012060 | 0.013619 | -0.004308 | 0.662482 | not_robust | - |
| `fund_count_change_absolute` | -0.2 | 0.516661 | 0.010759 | 0.009909 | 0.017406 | - | -0.005079 | 0.662482 | not_robust | - |
| `fund_count_change_absolute` | 0 | 0.515094 | 0.000961 | -0.001571 | 0.017406 | - | -0.019554 | 0.916352 | not_robust | - |
| `fund_count_change_absolute` | 0.2 | 0.509612 | 0.003614 | -0.002493 | 0.018108 | 0.018596 | -0.012418 | 0.916352 | not_robust | - |
| `fund_count_change_absolute` | 0.5 | 0.495473 | 0.006412 | 0.002433 | 0.015929 | 0.021388 | -0.009571 | 0.857169 | not_robust | - |
| `fund_count_change_absolute` | 0.7 | 0.453209 | 0.001141 | 0.001128 | 0.001168 | -0.009462 | -0.010361 | 0.916352 | not_robust | - |
| `fund_count_change_absolute` | 0.9 | 0.234930 | -0.001542 | 0.002678 | -0.010466 | -0.014702 | -0.010671 | 0.916352 | not_robust | - |
| `fund_count_change_quantile` | 0.05 | 0.949411 | 0.007289 | 0.004524 | 0.012780 | 0.003679 | -0.004844 | 0.624365 | not_robust | - |
| `fund_count_change_quantile` | 0.1 | 0.899417 | 0.005177 | 0.004080 | 0.007356 | 0.000131 | -0.006374 | 0.624365 | not_robust | - |
| `fund_count_change_quantile` | 0.2 | 0.799757 | 0.000845 | -0.000653 | 0.003821 | -0.005275 | -0.007341 | 0.905119 | not_robust | - |
| `fund_count_change_quantile` | 0.3 | 0.699595 | 0.000833 | -0.002234 | 0.006926 | -0.006440 | -0.006277 | 0.905119 | not_robust | - |
| `fund_count_change_quantile` | 0.4 | 0.600220 | 0.000000 | -0.000779 | 0.001547 | -0.005660 | -0.007042 | 0.999932 | not_robust | - |
| `fund_count_change_quantile` | 0.5 | 0.500460 | -0.003838 | -0.005519 | -0.000498 | -0.008079 | -0.011010 | 0.624365 | not_robust | - |
| `fund_count_change_quantile` | 0.6 | 0.400689 | -0.004632 | -0.007522 | 0.001109 | -0.004207 | -0.013651 | 0.624365 | not_robust | - |
| `fund_count_change_quantile` | 0.7 | 0.300512 | -0.003137 | -0.003803 | -0.001815 | -0.007250 | -0.011163 | 0.726969 | not_robust | - |
| `fund_count_change_quantile` | 0.8 | 0.201053 | -0.002583 | -0.000001 | -0.007712 | -0.013251 | -0.011678 | 0.762594 | not_robust | - |
| `fund_count_change_quantile` | 0.9 | 0.100885 | 0.002967 | 0.003137 | 0.002630 | 0.007251 | -0.008701 | 0.762594 | not_robust | - |
| `fund_count_change_quantile` | 0.95 | 0.050589 | 0.007113 | 0.012799 | -0.004181 | 0.000704 | -0.007336 | 0.624365 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.2 | 0.960521 | -0.004218 | -0.010408 | 0.007329 | -0.000389 | -0.021980 | 0.789387 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.15 | 0.901395 | -0.010385 | -0.012031 | -0.007113 | -0.019551 | -0.023207 | 0.272898 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.1 | 0.772756 | 0.000730 | -0.001741 | 0.005639 | 0.002580 | -0.009509 | 0.869746 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.075 | 0.664372 | 0.010294 | 0.011268 | 0.008361 | 0.004371 | 0.001875 | 0.023491 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.05 | 0.508108 | 0.010481 | 0.013729 | 0.004027 | 0.006329 | 0.002020 | 0.023491 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.03 | 0.334164 | -0.001693 | 0.005288 | -0.015561 | -0.021976 | -0.010269 | 0.789387 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.02 | 0.233365 | -0.002327 | 0.003439 | -0.013779 | -0.027112 | -0.013056 | 0.789387 | not_robust | - |
| `fund_holding_ratio_absolute` | -0.01 | 0.126567 | -0.003047 | 0.001934 | -0.012941 | -0.035642 | -0.014784 | 0.789387 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.05 | 0.948772 | -0.010308 | -0.014915 | -0.001155 | -0.010138 | -0.026909 | 0.315502 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.1 | 0.899315 | -0.015129 | -0.015887 | -0.013623 | -0.015295 | -0.029250 | 0.077979 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.2 | 0.799623 | 0.001297 | 0.004526 | -0.005117 | -0.006573 | -0.007140 | 0.815558 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.3 | 0.699833 | 0.005195 | 0.008925 | -0.002213 | -0.002938 | -0.003506 | 0.315502 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.4 | 0.599845 | 0.010840 | 0.015135 | 0.002309 | 0.003345 | 0.002588 | 0.050219 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.5 | 0.500635 | 0.010237 | 0.015311 | 0.000157 | 0.004856 | 0.000915 | 0.063545 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.6 | 0.400845 | 0.008127 | 0.012369 | -0.000300 | 0.000961 | -0.000237 | 0.087068 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.7 | 0.300363 | 0.003245 | 0.006752 | -0.003721 | -0.007852 | -0.004457 | 0.511178 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.8 | 0.201067 | -0.000541 | 0.002852 | -0.007280 | -0.016430 | -0.008290 | 0.890930 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.9 | 0.100882 | -0.003967 | -0.000893 | -0.010074 | -0.019938 | -0.014736 | 0.512191 | not_robust | - |
| `fund_holding_ratio_quantile` | 0.95 | 0.051326 | -0.005802 | -0.005308 | -0.006784 | -0.030991 | -0.019105 | 0.511178 | not_robust | - |
| `fund_holding_change_absolute` | -3 | 0.933699 | 0.017130 | 0.008457 | 0.038691 | 0.035427 | 0.003853 | 0.021646 | not_robust | - |
| `fund_holding_change_absolute` | -2 | 0.889846 | 0.016086 | 0.014197 | 0.021199 | -0.000009 | 0.002538 | 0.021646 | not_robust | - |
| `fund_holding_change_absolute` | -1 | 0.779440 | -0.001487 | -0.002831 | 0.002152 | -0.024139 | -0.014859 | 0.847258 | not_robust | - |
| `fund_holding_change_absolute` | -0.5 | 0.645341 | 0.004011 | -0.005186 | 0.023829 | 0.022016 | -0.009356 | 0.645262 | not_robust | - |
| `fund_holding_change_absolute` | 0 | 0.510128 | 0.004196 | -0.003973 | 0.020424 | 0.013032 | -0.008153 | 0.645262 | not_robust | - |
| `fund_holding_change_absolute` | 0.5 | 0.228340 | 0.007400 | 0.006534 | 0.009572 | -0.007632 | -0.002455 | 0.290211 | not_robust | - |
| `fund_holding_change_absolute` | 0.8 | 0.045852 | 0.001125 | -0.003995 | 0.012843 | -0.005473 | -0.012251 | 0.847258 | not_robust | - |
| `fund_holding_change_quantile` | 0.05 | 0.948714 | 0.016287 | 0.009546 | 0.029677 | 0.040753 | 0.002347 | 0.092693 | not_robust | - |
| `fund_holding_change_quantile` | 0.1 | 0.899296 | 0.005667 | -0.001136 | 0.019181 | 0.021076 | -0.003750 | 0.650204 | not_robust | - |
| `fund_holding_change_quantile` | 0.2 | 0.799624 | 0.005794 | 0.007620 | 0.002168 | 0.000355 | -0.001572 | 0.533180 | not_robust | - |
| `fund_holding_change_quantile` | 0.3 | 0.699868 | 0.002789 | 0.007358 | -0.006287 | -0.013759 | -0.003919 | 0.821078 | not_robust | - |
| `fund_holding_change_quantile` | 0.4 | 0.599875 | 0.001971 | 0.005994 | -0.006021 | -0.014952 | -0.005520 | 0.847032 | not_robust | - |
| `fund_holding_change_quantile` | 0.5 | 0.500628 | 0.003381 | 0.006724 | -0.003260 | -0.013077 | -0.004210 | 0.821078 | not_robust | - |
| `fund_holding_change_quantile` | 0.6 | 0.400850 | 0.000874 | 0.002076 | -0.001514 | -0.010554 | -0.006823 | 0.876890 | not_robust | - |
| `fund_holding_change_quantile` | 0.7 | 0.300345 | 0.001306 | 0.003020 | -0.002099 | -0.007456 | -0.006284 | 0.865677 | not_robust | - |
| `fund_holding_change_quantile` | 0.8 | 0.201101 | 0.001503 | 0.002766 | -0.001005 | -0.009831 | -0.006630 | 0.865677 | not_robust | - |
| `fund_holding_change_quantile` | 0.9 | 0.100917 | -0.000271 | -0.000710 | 0.000602 | -0.009846 | -0.010441 | 0.950152 | not_robust | - |
| `fund_holding_change_quantile` | 0.95 | 0.051389 | 0.003222 | 0.000015 | 0.009594 | -0.009387 | -0.008621 | 0.847032 | not_robust | - |
| `margin_change_absolute` | -0.3 | 0.952021 | -0.003067 | -0.009782 | 0.010028 | 0.001330 | -0.019153 | 0.630446 | not_robust | - |
| `margin_change_absolute` | -0.2 | 0.915290 | -0.007394 | -0.012469 | 0.002503 | 0.012023 | -0.020322 | 0.630446 | not_robust | - |
| `margin_change_absolute` | -0.1 | 0.826324 | 0.002513 | -0.002429 | 0.012330 | 0.015832 | -0.007095 | 0.630446 | not_robust | - |
| `margin_change_absolute` | 0 | 0.549380 | 0.013151 | 0.010057 | 0.019296 | 0.021644 | 0.005844 | 0.000089 | not_robust | - |
| `margin_change_absolute` | 0.05 | 0.297670 | 0.002283 | -0.000210 | 0.007235 | 0.015572 | -0.005797 | 0.630446 | not_robust | - |
| `margin_change_absolute` | 0.1 | 0.139249 | -0.002946 | -0.004862 | 0.000859 | 0.001545 | -0.013104 | 0.630446 | not_robust | - |
| `margin_change_absolute` | 0.2 | 0.033160 | 0.004722 | 0.006446 | 0.001296 | -0.005967 | -0.011759 | 0.630446 | not_robust | - |
| `margin_change_quantile` | 0.05 | 0.949292 | -0.005203 | -0.016624 | 0.017482 | 0.019838 | -0.020562 | 0.696583 | not_robust | - |
| `margin_change_quantile` | 0.1 | 0.899200 | -0.002092 | -0.008041 | 0.009725 | 0.010773 | -0.014283 | 0.821740 | not_robust | - |
| `margin_change_quantile` | 0.2 | 0.799342 | 0.005394 | 0.001586 | 0.012958 | 0.020660 | -0.003817 | 0.309749 | not_robust | - |
| `margin_change_quantile` | 0.3 | 0.699538 | 0.007936 | 0.006916 | 0.009962 | 0.015934 | 0.000287 | 0.047287 | robust_plateau | 0.000568 |
| `margin_change_quantile` | 0.4 | 0.599753 | 0.008450 | 0.010185 | 0.005004 | 0.006579 | 0.001990 | 0.025552 | robust_plateau | 0.002021 |
| `margin_change_quantile` | 0.5 | 0.500523 | 0.008998 | 0.009239 | 0.008519 | 0.012111 | 0.002573 | 0.025552 | robust_plateau | 0.002451 |
| `margin_change_quantile` | 0.6 | 0.400352 | 0.005296 | 0.004182 | 0.007507 | 0.012084 | -0.001365 | 0.296842 | robust_plateau | - |
| `margin_change_quantile` | 0.7 | 0.300462 | 0.002653 | 0.000256 | 0.007413 | 0.014778 | -0.004373 | 0.696583 | not_robust | - |
| `margin_change_quantile` | 0.8 | 0.200763 | -0.001327 | -0.003216 | 0.002425 | 0.009771 | -0.011379 | 0.821740 | not_robust | - |
| `margin_change_quantile` | 0.9 | 0.100800 | -0.003318 | -0.004516 | -0.000938 | 0.000231 | -0.015849 | 0.727761 | not_robust | - |
| `margin_change_quantile` | 0.95 | 0.050708 | -0.001283 | 0.001706 | -0.007219 | -0.016223 | -0.016019 | 0.836426 | not_robust | - |
| `holder_net_buy_ratio_absolute` | -3 | 0.987454 | 0.007244 | -0.003097 | 0.026315 | 0.014747 | -0.020082 | 0.591308 | not_robust | - |
| `holder_net_buy_ratio_absolute` | -1 | 0.952504 | -0.001123 | -0.008239 | 0.013013 | 0.009546 | -0.018052 | 0.887385 | not_robust | - |
| `holder_net_buy_ratio_absolute` | 0 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.021239 | 0.466008 | not_robust | - |
| `holder_net_buy_ratio_absolute` | 0.5 | 0.016634 | -0.017315 | -0.018450 | -0.015082 | -0.020998 | -0.036174 | 0.038886 | not_robust | - |
| `holder_net_buy_ratio_absolute` | 1 | 0.009969 | -0.021204 | -0.022192 | -0.019121 | -0.021649 | -0.037118 | 0.024795 | not_robust | - |
| `holder_net_buy_ratio_absolute` | 2 | 0.004583 | -0.020845 | -0.019005 | -0.024611 | -0.002772 | -0.039770 | 0.024795 | not_robust | - |
| `holder_net_buy_ratio_absolute` | 5 | 0.000466 | 0.024817 | 0.026315 | 0.020388 | - | -0.001910 | 0.162753 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.05 | 0.951112 | -0.005116 | -0.013002 | 0.010547 | 0.006579 | -0.022849 | 0.487177 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.1 | 0.913501 | -0.007532 | -0.012374 | 0.002086 | 0.004128 | -0.022092 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.2 | 0.899313 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.020716 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.3 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.021217 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.4 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.021368 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.5 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.021615 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.6 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.020827 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.7 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.022524 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.8 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.021158 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.9 | 0.899311 | -0.006410 | -0.010993 | 0.002694 | 0.005197 | -0.020767 | 0.406833 | not_robust | - |
| `holder_net_buy_ratio_quantile` | 0.95 | 0.535499 | -0.004573 | -0.008390 | 0.003010 | 0.003116 | -0.018269 | 0.487177 | not_robust | - |
| `holder_net_buy_count_absolute` | -2 | 0.964468 | -0.004446 | -0.014923 | 0.015671 | 0.007815 | -0.025161 | 0.727173 | not_robust | - |
| `holder_net_buy_count_absolute` | 0 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.022469 | 0.727173 | not_robust | - |
| `holder_net_buy_count_absolute` | 1 | 0.050062 | -0.007464 | -0.009037 | -0.004339 | -0.016691 | -0.020419 | 0.727173 | not_robust | - |
| `holder_net_buy_count_absolute` | 2 | 0.019347 | -0.017076 | -0.024738 | -0.001855 | -0.007092 | -0.033072 | 0.070598 | not_robust | - |
| `holder_net_buy_count_absolute` | 3 | 0.008708 | -0.003920 | -0.002235 | -0.006969 | -0.007880 | -0.020446 | 0.727173 | not_robust | - |
| `holder_net_buy_count_absolute` | 5 | 0.002682 | -0.001498 | 0.009795 | -0.016269 | -0.030974 | -0.016893 | 0.855299 | not_robust | - |
| `holder_net_buy_count_absolute` | 10 | 0.000330 | 0.006677 | 0.013370 | -0.001592 | - | -0.024127 | 0.727173 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.05 | 0.960442 | -0.004114 | -0.013297 | 0.014128 | 0.008647 | -0.023156 | 0.618922 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.1 | 0.926952 | -0.006722 | -0.012143 | 0.004046 | -0.000043 | -0.023219 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.2 | 0.898688 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.021358 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.3 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.023198 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.4 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.022434 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.5 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.022068 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.6 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.022024 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.7 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.022618 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.8 | 0.898412 | -0.006736 | -0.013973 | 0.007639 | 0.006151 | -0.021407 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.9 | 0.895678 | -0.005502 | -0.012118 | 0.007639 | 0.006151 | -0.019046 | 0.487933 | not_robust | - |
| `holder_net_buy_count_quantile` | 0.95 | 0.489495 | -0.002913 | -0.009320 | 0.009813 | 0.007865 | -0.016475 | 0.618922 | not_robust | - |
| `pledge_change_absolute` | -1 | 0.964625 | -0.015007 | -0.023082 | 0.000677 | 0.018181 | -0.034498 | 0.250846 | not_robust | - |
| `pledge_change_absolute` | -0.5 | 0.943024 | -0.008804 | -0.013560 | 0.000245 | 0.013272 | -0.027436 | 0.425271 | not_robust | - |
| `pledge_change_absolute` | 0 | 0.837281 | 0.002474 | -0.000419 | 0.007977 | 0.027565 | -0.011679 | 0.705389 | not_robust | - |
| `pledge_change_absolute` | 0.2 | 0.105430 | 0.014239 | 0.012145 | 0.018414 | 0.017540 | -0.002929 | 0.250846 | not_robust | - |
| `pledge_change_absolute` | 0.5 | 0.071382 | 0.009095 | 0.005991 | 0.015286 | 0.025606 | -0.002824 | 0.348545 | robust_plateau | - |
| `pledge_change_absolute` | 1 | 0.043813 | 0.011006 | 0.014957 | 0.003274 | 0.039167 | -0.006743 | 0.328660 | robust_plateau | - |
| `pledge_change_absolute` | 2 | 0.021410 | 0.016503 | 0.010507 | 0.028224 | 0.056565 | -0.005704 | 0.250846 | robust_plateau | - |
| `pledge_change_quantile` | 0.05 | 0.949034 | -0.013225 | -0.023692 | 0.006689 | 0.022213 | -0.031566 | 0.827880 | not_robust | - |
| `pledge_change_quantile` | 0.1 | 0.900978 | 0.001584 | 0.005134 | -0.005170 | 0.008013 | -0.015337 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.2 | 0.842220 | -0.000597 | -0.002983 | 0.003943 | 0.020636 | -0.013928 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.3 | 0.837338 | 0.002231 | -0.000419 | 0.007273 | 0.027565 | -0.011194 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.4 | 0.837281 | 0.002474 | -0.000419 | 0.007977 | 0.027565 | -0.010860 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.5 | 0.837281 | 0.002474 | -0.000419 | 0.007977 | 0.027565 | -0.012152 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.6 | 0.837281 | 0.002474 | -0.000419 | 0.007977 | 0.027565 | -0.011659 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.7 | 0.832811 | 0.001502 | -0.001902 | 0.007977 | 0.027565 | -0.012732 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.8 | 0.628564 | 0.000532 | -0.003427 | 0.008064 | 0.024620 | -0.014056 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.9 | 0.116259 | 0.005019 | -0.001210 | 0.016869 | 0.022231 | -0.010417 | 0.934183 | not_robust | - |
| `pledge_change_quantile` | 0.95 | 0.066961 | 0.014066 | 0.013617 | 0.014919 | 0.036215 | -0.003444 | 0.827880 | not_robust | - |
| `insider_buy_holding` | 5 | 0.006441 | -0.019495 | -0.024743 | -0.008184 | 0.005413 | -0.036487 | 0.029161 | not_robust | - |
| `insider_buy_holding` | 10 | 0.010894 | -0.014209 | -0.019737 | -0.003220 | -0.008808 | -0.030940 | 0.089400 | not_robust | - |
| `insider_buy_holding` | 20 | 0.018845 | -0.007501 | -0.016224 | 0.009627 | 0.012177 | -0.021201 | 0.306034 | not_robust | - |
| `insider_buy_holding` | 40 | 0.032797 | -0.004943 | -0.012455 | 0.009979 | 0.016127 | -0.018444 | 0.394208 | not_robust | - |
| `insider_buy_holding` | 60 | 0.045127 | -0.008109 | -0.010138 | -0.004080 | -0.015643 | -0.019619 | 0.209449 | not_robust | - |
| `lhb_net_buy_holding` | 5 | 0.007381 | -0.002125 | -0.004131 | 0.001836 | -0.000611 | -0.016856 | 0.729620 | not_robust | - |
| `lhb_net_buy_holding` | 10 | 0.013516 | 0.005880 | 0.008445 | 0.000825 | 0.012139 | -0.009351 | 0.638747 | not_robust | - |
| `lhb_net_buy_holding` | 20 | 0.024367 | 0.011321 | 0.018317 | -0.002559 | 0.002023 | -0.002712 | 0.385671 | not_robust | - |
| `lhb_net_buy_holding` | 40 | 0.042736 | 0.004975 | 0.008526 | -0.002070 | 0.006480 | -0.010463 | 0.638747 | not_robust | - |
| `lhb_net_buy_holding` | 60 | 0.058236 | 0.003679 | 0.008447 | -0.005781 | -0.014086 | -0.009267 | 0.638747 | not_robust | - |
| `repurchase_holding` | 5 | 0.004218 | 0.012370 | 0.016499 | 0.006421 | 0.008605 | -0.004073 | 0.121836 | robust_plateau | - |
| `repurchase_holding` | 10 | 0.008416 | 0.016651 | 0.019176 | 0.012444 | 0.005044 | -0.000429 | 0.075915 | robust_plateau | - |
| `repurchase_holding` | 20 | 0.016591 | 0.016842 | 0.016169 | 0.018118 | -0.000945 | 0.000244 | 0.075915 | robust_plateau | 0.000001 |
| `repurchase_holding` | 40 | 0.032118 | 0.011246 | 0.010047 | 0.013607 | 0.007346 | -0.004800 | 0.121836 | robust_plateau | - |
| `repurchase_holding` | 60 | 0.046801 | 0.014169 | 0.014196 | 0.014117 | 0.003501 | -0.001001 | 0.075995 | robust_plateau | - |
| `block_premium_holding` | 5 | 0.004343 | -0.014556 | -0.018893 | -0.011318 | -0.020356 | -0.027706 | 0.050612 | not_robust | - |
| `block_premium_holding` | 10 | 0.007500 | -0.019337 | -0.018976 | -0.019601 | -0.029596 | -0.033894 | 0.008486 | not_robust | - |
| `block_premium_holding` | 20 | 0.012434 | -0.014666 | -0.004975 | -0.021752 | -0.031666 | -0.024666 | 0.008486 | not_robust | - |
| `block_premium_holding` | 40 | 0.019490 | -0.006023 | 0.003077 | -0.012677 | -0.010956 | -0.015039 | 0.216854 | not_robust | - |
| `block_premium_holding` | 60 | 0.024363 | -0.004829 | 0.003329 | -0.010793 | -0.005643 | -0.014816 | 0.273704 | not_robust | - |
| `analyst_rating_change_holding` | 5 | 0.006683 | -0.008813 | -0.005958 | -0.015200 | -0.012299 | -0.021465 | 0.314305 | not_robust | - |
| `analyst_rating_change_holding` | 10 | 0.013023 | -0.009319 | -0.008188 | -0.011610 | -0.027924 | -0.023674 | 0.314305 | not_robust | - |
| `analyst_rating_change_holding` | 20 | 0.025129 | -0.003456 | -0.002831 | -0.004696 | -0.011540 | -0.015559 | 0.730457 | not_robust | - |
| `analyst_rating_change_holding` | 40 | 0.047458 | -0.001309 | 0.001744 | -0.007359 | -0.003242 | -0.011707 | 0.804976 | not_robust | - |
| `analyst_rating_change_holding` | 60 | 0.067129 | -0.010990 | -0.005739 | -0.021396 | -0.025961 | -0.021021 | 0.121852 | not_robust | - |

## 判定口径

- 绝对阈值和分位阈值分别成族；分位阈值按每个交易日横截面计算。
- 初筛要求稳健平台、全样本/训练/检验方向 IC 为正、行业+规模中性 IC 为正、族内 BH-FDR `q <= 0.10`、1000 次 block-20 bootstrap `ci_low > 0`。
- 决赛使用 5000 次 bootstrap，主 block=20；block 10/40 只做敏感性报告。
- 推荐排序先最大化 `min(train_ic, test_ic)`，再最大化决赛 CI，最后选择更接近参考参数者。
- 单点或极窄平台必须拒绝。
