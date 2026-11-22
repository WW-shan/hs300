# R471 因子研究前沿对账

本轮只对账 R464-R470 的冻结证据，不重新跑参数、不改变任何拒绝结论。

| 项目 | 结果 |
| --- | --- |
| 因子库 | 235 个，118 个有研究证据 |
| R418 严格准入 | 5 个 |
| R450 中性补充准入 | 2/5 个 |
| 当前 active_version | null |
| 可用无杠杆最终策略 | 无 |

## 当前最接近候选

| 候选 | 检验年化 | 近一年年化 | 检验 p05 | 近一年 p05 | 状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| r463_four_vq_10_25 | 22.23% | 9.59% | 3.82% | -12.80% | rejected_recent_tail |
| r464_seven_vq_5pct | 8.09% | 1.90% | -11.06% | -17.40% | rejected_validation_and_tail |
| r468_inventory_growth_low | 37.81% | 7.64% | 2.20% | -19.75% | rejected_validation_and_concentration |
| r470_vqs_20_30 | 17.07% | 5.57% | -7.98% | -21.91% | rejected_mdd_and_tail |

## 负知识

- r464: joint sleeve/portfolio MC: validation bootstrap zero positives
- r465: tail persists under block 10/20/40
- r466: sleeve risk budget rejected; 34/160 samples degenerate
- r467: after removing degeneracy: 123 eligible, zero bootstrap positives
- r468: operating-efficiency standalone rejected on validation bootstrap and recent tail
- r469: top 10 profitable trades removal leaves only ~2.3% of total PnL
- r470: value_quality_safety standalone rejected on MDD and both bootstrap tails

## 下一步

- 不再重复现金缓冲、固定止损、简单 regime 防守、防御 ETF 混合和 sleeve 风险预算。
- 继续寻找有独立经济含义的新收益源，先做单因子真实 T+1、验证 bootstrap、近一年尾部和集中度归因。
- 新候选只有全部门槛通过才进入观察集；否则保留负知识。

## 边界

- This report reuses frozen evidence and does not rerun statistics.
- A rejected candidate is not made usable by being summarized here.
- Current HS300 membership and industry classification retain survivorship bias.
- No leverage and no production activation.
