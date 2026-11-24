# R531 因子库核心独立性与综合能力设计

## 目标

在 R530 的 11 个统一 PIT 准入候选上，去掉同一经济暴露的重复因子，构建一个仅研究用的因子库核心，并检验该核心在验证期和近一年是否仍有综合预测能力。

## 研究类型

`statistical_screen`

## 输入

- R530 result、preregistration 及其 sidecar；
- R526 统一 PIT membership 和 industry history；
- R530 已确认的 11 个候选，以及它们冻结的 family、参数和 R530 统计结果。

不重新挑选参数，不重新使用 R530 的 rejected 候选。

## 聚类

- 只用训练期 2022-05-05 至 2024-12-31 的月末交易日。
- 对每对候选计算横截面 Spearman 相关，按日期计算后取均值。
- 绝对相关 `>= 0.70` 视为同一暴露，使用 union-find 聚类。
- R530 训练 IC 更高的候选作为每个聚类的代表；不允许根据验证期或近一年结果重新选代表。
- 聚类过程必须保留完整相关矩阵、聚类大小、代表因子和训练期依据。

## 组合核心

- 每个代表因子按日转成横截面百分位。
- 对同一 `symbol/date` 的可用代表因子百分位做等权平均，形成 `library_core_rank`。
- 只在 R526 每日 300 只成员内计算。
- 不使用杠杆、订单、纸面目标或主动组合权重。

## 门槛

核心统计准入：

- 至少 2 个独立聚类；
- 训练期中性 IC >= 0.01；
- 验证期中性 IC > 0；
- 近一年中性 IC > 0；
- 5000 次 10/20/40 block bootstrap 的 95% CI 下限均 > 0；
- 核心覆盖率 >= 0.90。

未通过则结论为 `rejected` 或 `inconclusive`，不得为了通过而改聚类阈值、代表规则或窗口。

## 产物

- `r531_factor_library_core_preregistration.json` + sidecar
- `r531_factor_library_core_result.json` + sidecar
- `r531_factor_library_core_result.md` + sidecar

结果包括相关矩阵、聚类、代表因子、核心 IC/bootstrp、维度覆盖和边界。

## 非目标

- 不把因子统计核心当组合收益。
- 不重新使用 rejected 参数。
- 不提升 `active_version`，不生成执行产物。
