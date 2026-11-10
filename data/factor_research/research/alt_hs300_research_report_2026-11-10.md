# HS300 多维因子研究记录（2026-11-10）

## 结论

本轮没有直接上线新因子，完成的是从数据审计、横截面筛选、市场状态检验到参数
稳定性与边际贡献的可复核扩维：

1. 109 个扩展数值字段全部补上显式 taxonomy，102 个通过完整性审计，7 个保留 unknown。
2. 64 个个股、行业和事件级因子完成五年 5 日 Rank IC、近一年 IC、逐年同号率、
   Newey-West t、BH-FDR 以及行业和规模中性化。
3. 38 个市场级因子完成 5/20 日 HS300 前瞻收益关联；没有因子通过 5 日 FDR 0.10，
   目前只能作为 watch conditioner。
4. 8 个价格风险家族完成 32 组参数、训练/检验分段和 1000 次 moving-block Monte Carlo。
5. 物化 3 个参数候选：`idio_vol_20d_low`、`idio_vol_40d_low`、`vol_of_vol_20d_low`。
6. 建立 `multi_dimension_v1` 八成员 watch 因子集，`active_version=null`，不生成交易信号。

## 数据审计

- 审计 ID：`sha256:ff17619afa8d7a5d3c4c2e2abbd459a87787e2a0ece632323b30eacc66decf2d`
- 扩展字段：109
- 完整性通过：102
- unknown：7
- 因子母集：223
- 最新扩展分区：2026-11-05
- 审计 manifest 只记录仓库相对路径，不再写入机器绝对路径。

未知项：`mainline_top1_flag`、`mainline_strength`、`mainline_leader_flag`（PIT/覆盖
不足），`pmi`、`cpi_yoy`、`ppi_yoy`（陈旧），`northbound_net_buy_20d`
（2024-08-16 后无有效值）。

审计使用交易日陈旧度；对以 0 填充缺口的事件因子，使用“最新有效非零日期”判断
断档，避免零值掩盖数据缺失。

## 横截面结果

样本为 2021-06-01 至 2026-11-05，当前 HS300 名单，5 日 close-to-close 前瞻收益，
最小截面 30 只。64 个因子中：12 个 promising、24 个 watch、11 个中性化后 watch、
其余为 weak/inconclusive。

| 因子 | 原始方向 IC | 行业+规模 IC | 近一年 IC | 逐年同号率 | q 值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `idio_vol_40d_low` | 0.0426 | 0.0260 | 0.0465 | 0.833 | 0.0170 |
| `dividend_yield` | 0.0422 | 0.0329 | 0.0596 | 0.833 | 0.0170 |
| `idio_vol_20d_low` | 0.0409 | 0.0249 | 0.0459 | 0.833 | 0.0170 |
| `idio_vol_60d_low` | 0.0397 | 0.0269 | 0.0455 | 0.833 | 0.0182 |
| `cs_spread_60d` | 0.0387 | 0.0228 | 0.0397 | 0.833 | 0.0170 |
| `vol_of_vol_20d_low` | 0.0303 | 0.0177 | 0.0491 | 0.833 | 0.0184 |
| `fund_holding_ratio_low` | 0.0255 | 0.0108 | 0.0259 | 0.833 | 0.0170 |
| `cash_flow_stability_4q_low` | 0.0228 | 0.0096 | 0.0343 | 0.800 | 0.0170 |
| `ocf_margin_4q` | 0.0200 | 0.0109 | 0.0412 | 0.800 | 0.0170 |
| `margin_chg_1m_low` | 0.0164 | 0.0094 | 0.0133 | 1.000 | 0.0170 |
| `lhb_event_count_20d_low` | 0.0160 | -0.0008 | 0.0255 | 1.000 | 0.0504 |
| `holder_top10_share_chg_yoy` | 0.0116 | 0.0049 | 0.0192 | 1.000 | 0.0355 |

`lhb_event_count_20d_low` 原始 IC 为正，但行业和规模中性后接近 0，不进入正式
候选集。`cs_spread_60d` 虽独立 IC 较高，但与低波腿高度相关，也在边际贡献环节
被剔除。

## 市场状态因子

38 个市场级因子对 HS300 5/20 日前瞻收益做 rank 回归；5 日 BH-FDR 0.10 下没有
因子通过。相对靠前的 `if_net_short_chg_1m`、`equity_risk_premium`、
`ah_premium_chg_1m`、`m1_m2_scissors` 只能保留为 watch conditioner。

| 因子 | 5 日方向 beta | NW t | 近一年 beta | 20 日 beta | q 值 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `if_net_short_chg_1m` | 0.1213 | 2.454 | 0.1920 | 0.1224 | 0.2207 |
| `equity_risk_premium` | 0.1172 | 2.378 | -0.0309 | 0.2622 | 0.2207 |
| `ah_premium_chg_1m` | 0.1070 | -2.250 | -0.1545 | 0.1780 | 0.2322 |
| `m1_m2_scissors` | 0.0626 | 1.218 | 0.0823 | 0.0967 | 0.6039 |

`qvix_z_252` 的样本内方向与标签方向相反，不能直接按原方向使用；`margin_safety_*`
也有同样问题。

## 参数蒙特卡洛

采用 2021-2024 训练、2025-2026 检验、5 日重叠收益 HAC 和 20 日 moving-block
bootstrap（1000 次）。参数网格只代表相邻窗口的相关实验，不把 32 个点当成 32 个
独立发现。

| 家族 | 当前参数 | 推荐参数 | 当前全样本 IC | 当前检验段 IC | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 残差低波 | 60 | 20 | 0.0397 | 0.0199 | 20/40/60/120 全部同号；20 的训练/检验最平衡，40 全样本 IC 最高 |
| Corwin-Schultz 价差 | 60 | 60 | 0.0387 | 0.0195 | 40/60/120 稳定，保留 60 |
| 波动率稳定性 | 60 | 20 | 0.0249 | 0.0280 | 20 日版本全样本 bootstrap 下界为正，优于 60 日 |
| 市场下跌 beta | 60 | 无 | 0.0095 | 0.0013 | 检验段接近 0，不推荐 |
| 价格延迟 | 60 | 无 | 0.0098 | -0.0017 | 检验段转负，不推荐 |
| 趋势效率 | 60 | 无 | -0.0144 | -0.0014 | 全样本和检验段均为负，不推荐 |
| 风险调整动量 | 60 | 无 | -0.0148 | -0.0014 | 全样本和检验段均为负，不推荐 |
| beta 不对称 | 120 | 无 | -0.0178 | -0.0325 | 检验段为负，不推荐 |

原 60 日字段保留，新增 `idio_vol_20d_low`、`idio_vol_40d_low`、
`vol_of_vol_20d_low`，不覆盖历史定义。多维集合采用 40 日残差低波作为代表，
原因是其全样本原始 IC 最高；20 日版本作为同家族备选，不重复加入集合。

## 相关性与边际贡献

在 `core_v1 v2` 四条 alpha 腿上加入单因子 20% 权重做边际 IC：

| 因子 | 边际 IC | 近一年边际 IC | p 值 | 平均绝对相关 | 最大相关 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `idio_vol_40d_low` | +0.0226 | +0.0267 | 0.00004 | 0.346 | 0.952 |
| `idio_vol_20d_low` | +0.0217 | +0.0253 | 0.00003 | 0.329 | 0.901 |
| `dividend_yield` | +0.0211 | +0.0301 | 0.00039 | 0.220 | 0.479 |
| `idio_vol_60d_low` | +0.0210 | +0.0264 | 0.00023 | 0.342 | 0.952 |
| `vol_of_vol_20d_low` | +0.0161 | +0.0254 | 0.00028 | 0.229 | 0.628 |
| `cash_flow_stability_4q_low` | +0.0129 | +0.0159 | 0.00006 | 0.083 | 0.185 |
| `fund_holding_ratio_low` | +0.0119 | +0.0098 | 0.00101 | 0.157 | 0.328 |
| `ocf_margin_4q` | +0.0118 | +0.0184 | 0.00025 | 0.129 | 0.594 |
| `margin_chg_1m_low` | +0.0085 | +0.0074 | 0.00010 | 0.052 | 0.113 |
| `lhb_event_count_20d_low` | +0.0079 | +0.0129 | 0.00164 | 0.074 | 0.182 |
| `holder_top10_share_chg_yoy` | +0.0070 | +0.0096 | 0.00036 | 0.040 | 0.074 |
| `cs_spread_60d` | -0.0200 | -0.0218 | 0.00023 | 0.298 | 0.820 |

## 新建 watch 集

`multi_dimension_v1` 版本 1，8 个成员，等权 0.125：

1. `idio_vol_40d_low`
2. `dividend_yield`
3. `vol_of_vol_20d_low`
4. `cash_flow_stability_4q_low`
5. `fund_holding_ratio_low`
6. `ocf_margin_4q`
7. `margin_chg_1m_low`
8. `holder_top10_share_chg_yoy`

状态：`state=watch`，`active_version=null`。`core_v1` 同步刷新为 `v2` watch；
此次升级只刷新 taxonomy 和定义指纹，不改变经济含义及权重。

## 限制

- 历史全段使用当前 HS300 名单，存在幸存者偏差。
- 5 日 IC 使用重叠收益，HAC 已按 lag=5 处理，但样本内共同暴露仍存在。
- 财务字段目前是按公告或法定披露日生效的声明证据，尚未完成逐字段 PIT 对照验证。
- 参数扫描是相关网格，不是 32 个独立发现；Monte Carlo 只评估日 IC 分布，不替代
  真实组合回测。
- 尚未完成 `multi_dimension_v1` 的真实组合回测、2x/3x 成本、容量、停牌/涨跌停
  和滚动一年检验，不能据此宣称可交易或可上线。

## 产物

- 数据审计：`data/factor_research/audits/latest_alt_hs300_summary.json`
- 横截面 IC：`data/factor_research/research/alt_hs300_rank_ic_5d_2026-11-10_v2.json`
- 市场状态扫描：`data/factor_research/research/alt_hs300_regime_5d_20d_2026-11-10_v2.json`
- 参数蒙特卡洛：`data/factor_research/research/alt_hs300_parameter_monte_carlo_2026-11-10.json`
- 相关性与边际贡献：`data/factor_research/research/alt_hs300_incremental_2026-11-10.json`
- 多维 watch 集：`data/factor_research/sets/multi_dimension_v1/versions/v0001.json`
