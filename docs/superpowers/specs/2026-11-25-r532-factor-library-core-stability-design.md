# R532 因子库核心时间稳定性与综合能力设计

## 目标

在 R531 已冻结的 6 个独立代表因子上，检查综合核心是否跨年份、跨滚动 252 日窗口和近一年仍然稳定；用固定种子的 moving-block bootstrap 检验核心 IC 均值，并给出每个代表因子的留一贡献。本轮不重新选参数、不改聚类、不根据验证期结果替换代表因子。

## 研究类型

`statistical_screen`。只产生因子统计证据，不产生组合收益、订单、纸面目标或生产版本。

## 输入链

- R530 预注册与结果；
- R531 预注册与结果；
- R526 逐日 HS300 membership 与 industry history；
- 项目本地 enriched 行情和 R530 使用的同一套因子求值器。

所有输入必须通过 sidecar 校验。R531 必须为 `admitted_for_research`，代表因子必须恰好 6 个且每个都与 R531 冻结聚类一致。R532 只复用这些代表因子及其 R530 冻结参数。

## 固定窗口

- 评估区间：2022-05-05 至 2026-11-05；
- 训练：2022-05-05 至 2024-12-31；
- 样本外验证：2025-01-01 至 2026-11-05；
- 近一年：2025-10-14 至 2026-11-05；
- 预测周期：5 个交易日；
- 中性化：行业 + 规模；
- 滚动窗口：252 个有效 IC 交易日，每 21 个有效交易日推进一窗；
- 最少完整滚动窗口：24。

## 蒙特卡洛

- full、validation、recent 三个区间分别做 5000 次 moving-block bootstrap；
- block size 固定为 10、20、40；
- 每个滚动 252 日窗口用 block size 20 做 5000 次 bootstrap；
- 所有 seed 由 `R532 + 窗口身份 + block size + 次数` 确定，不得运行时更换；
- bootstrap 只重采样已经实现的日度 IC，不重算因子，不生成收益序列。

## 综合能力诊断

- 年度 IC：完整展示 2022-2026；
- 滚动窗口：均值 IC、正 IC 窗口比例、中位数、最差窗口、窗口 bootstrap CI 为正比例；
- 留一因子核心：依次移除一个代表因子，重新计算等权核心 IC，报告 full/validation/recent 的配对 IC 差；
- 留一结果仅用于解释贡献和集中度，不在本轮据此删除因子。

## 门槛

只有全部满足才标记 `admitted_for_research`：

- R531 冻结链有效且代表因子数为 6；
- 核心覆盖率 >= 0.90；
- full 中性 IC >= 0.01；
- validation 中性 IC > 0；
- recent 中性 IC > 0；
- 年度正 IC 年数至少 4/5；
- full/validation/recent 的 10/20/40 block bootstrap 95% CI 下限均 > 0；
- 完整滚动窗口数 >= 24；
- 滚动窗口中位 IC > 0；
- 滚动窗口正 IC 比例 >= 0.70；
- 最差完整滚动窗口 IC >= -0.01；
- 滚动窗口 bootstrap 95% CI 下限为正的比例 >= 0.70；
- 最差滚动窗口 bootstrap CI 下限 >= -0.01。

这些阈值只排除“整体失效或明显不稳”，允许单个弱年份和少数弱窗口存在。不得在查看 R532 结果后调整阈值、窗口、block size、seed 或代表因子。

## 结论规则

- full、validation 或 recent 任一不满足方向要求：`rejected`；
- 数据不足，完整滚动窗口少于 24：`inconclusive`；
- 全部门槛通过：`admitted_for_research`；
- 其余稳定性门槛失败：`rejected`；
- 失败原因必须写入 `negative_knowledge`，不得只写在 Markdown。

## 产物

- `r532_factor_library_core_stability_preregistration.json` + sidecar；
- `r532_factor_library_core_stability_result.json` + sidecar；
- `r532_factor_library_core_stability_result.md` + sidecar；
- ResearchLedger 发布记录和更新后的资产台账。

## 非目标

- 不重新调因子参数；
- 不重新做相关性聚类；
- 不把 IC 稳定性写成组合收益；
- 不提升 `active_version`；
- 不生成杠杆、订单、纸面目标或生产策略。
