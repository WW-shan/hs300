# R530 统一 PIT 因子库蒙特卡洛复核设计

## 目标

在不重新挑选参数的前提下，复核现有因子库全部参数研究留下的 15 个推荐参数，在统一逐日 HS300 名单、统一行业 vintage 和统一 5 日预测窗口下是否仍然成立；同时给出整个因子库的覆盖、参数稳定性、统计显著性和维度能力矩阵。

## 研究类型

`statistical_screen`

本轮只产生因子统计证据，不产生组合收益或正式策略结论。

## 输入

- 因子目录：`data/factor_research/catalog/latest.json` 对应快照，235 个因子。
- 参数族：
  - P1 价格风险 8 族；
  - P2 内置价量 31 族；
  - P3 扩展数据 23 族；
  - P4 阈值/事件 23 族。
- 历史 MC：
  - `p1_parameter_mc_2026-11-11.json`
  - `p2_builtin_parameter_mc_2026-11-11_v2.json`
  - `p3_extended_parameter_mc_2026-11-11_v3.json`
  - `p4_threshold_parameter_mc_2026-11-11.json`
- 统一 PIT bundle：`r526_unified_pit_bundle_window/`。
- 价格与扩展输入：项目本地 `kline_daily_enriched`、`kline_index_daily`、`ext_data`、事件研究缓存。

## 固定研究窗口

- 正式评估：2022-05-05 至 2026-11-05。
- 训练：2022-05-05 至 2024-12-31。
- 验证：2025-01-01 至 2026-11-05。
- 近一年：2025-10-14 至 2026-11-05。
- 预测周期：5 个交易日。
- 最小横截面：30 个有效成员。

## 候选参数来源

候选不是本轮重新搜索，而是四份历史 MC 中 `recommendation_status=recommended` 的冻结参数：

- P1 推荐 3 个；
- P2 推荐 7 个；
- P3 推荐 1 个；
- P4 推荐 4 个。

必须断言总数 15、每个候选的 `recommended_parameter` 与历史结果一致、参数属于原参数族网格。禁止新增参数、禁止在结果后追加参数、禁止用本轮结果替换历史推荐。

## 统一 PIT 口径

- 名单：R526 `daily_snapshots.parquet`，每日 300 只，按 `query_date` 作为评估日。
- 行业：R526 `industry_history.parquet`，按 symbol 使用不晚于评估日的最后一个 `effective_date`。
- 因子值允许使用窗口前历史，但进入 IC 的行必须是当日名单成员。
- 行业中性化使用 PIT 行业列；规模中性化使用 `log(close * volume / turnover_rate)`，与历史参数研究一致。
- 所有候选使用同一个 5 日前向收益和同一组评估日期。

## 候选计算

对每个候选：

1. 从历史参数族中取已冻结参数；
2. 用原评估器计算整段历史因子值；
3. 与统一 PIT 名单、行业和规模字段连接；
4. 计算原始 IC、行业中性 IC、规模中性 IC、行业+规模中性 IC；
5. 计算 full/train/validation/recent 的 NW-t 和 p 值；
6. 对行业+规模中性 IC 做 5000 次 moving-block bootstrap，block 为 10、20、40；
7. 对候选原参数族的相邻点按同一 PIT 口径计算同号性，形成本轮 plateau 状态；
8. 对 15 个候选的 full p 值做 BH-FDR。

## 门槛

候选通过全部条件才是 `admitted_for_research`：

- 覆盖率 >= 0.90；
- full 中性 IC >= 0.01；
- train 中性 IC >= 0.01；
- validation 中性 IC > 0；
- recent 中性 IC > 0；
- BH-FDR q <= 0.10；
- bootstrap 10/20/40 三个 block 的 95% CI 下限均 > 0；
- 本轮相邻参数 plateau 为正；
- 原历史参数平台状态为 robust plateau。

只要 full 或 validation 中性 IC 不为正，结论为 `rejected`；数据不足或门槛组合不完整为 `inconclusive`；满足全部门槛为 `admitted_for_research`，但仅限研究准入，不自动进入 watch/active 组合。

## 输出

新增：

```text
data/factor_research/research/r530_unified_factor_library_mc_preregistration.json
data/factor_research/research/r530_unified_factor_library_mc_result.json
data/factor_research/research/r530_unified_factor_library_mc_result.md
```

JSON 必须包含：

- `library_census`: 因子数、参数族数、参数点覆盖、历史推荐/拒绝数、按 data_class 的能力分布；
- `candidates`: 15 个冻结参数候选的完整统计；
- `fdr`: 15 个候选的多重检验结果；
- `conclusion_counts`;
- `known_limits`;
- `output_policy`.

Markdown 只展示结论表、维度覆盖、失败原因和下一步，不展示未经证实的收益承诺。

## 不做

- 不重新调参；
- 不把统计准入写成组合收益；
- 不输出杠杆、订单、纸面目标或 `active_version`；
- 不修改历史 P1-P4 结果；
- 不将本轮 PIT 名单回填结果解释为未来收益保证。
