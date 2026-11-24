# R535 全参数族普查与训练期参数筛选设计

## 目标

覆盖因子库注册表中的全部 85 个可调参数族和 472 个参数点，在 R526 统一逐日 HS300 名单、行业 vintage、5 日预测收益和行业+规模中性口径下，先用训练期选择每族参数，再做 validation/recent/BH-FDR/5000 次 MC 复核。目标是发现可加入因子库的新维度，而不是继续只围绕 R530 的 15 个历史推荐。

## 研究类型

`statistical_screen`。不产生组合收益、订单、纸面目标或 active version。

## 输入

- R530 统一 PIT helper、参数注册表和 catalog census；
- `families.json` 62 个窗口/内置族；
- `thresholds.json` 23 个阈值/事件族；
- P1-P4 历史结果只用于确定 family 所属 source tier，不用于参数选择；
- R526 daily snapshots 与 industry history；
- 项目行情和扩展数据。

## 固定口径

- 2022-05-05 至 2026-11-05；
- 训练截止 2024-12-31；
- validation 从 2025-01-01；
- recent 从 2025-10-14；
- 5 日前向收益；
- 最少 30 只横截面；
- industry+size 中性化；
- 覆盖率 >= 0.90。

## 每族参数选择

对每个参数点计算训练期中性 IC、training ICIR、coverage 和训练期 block-20 bootstrap 1000 次。

训练资格：

- coverage >= 0.90；
- train 中性 IC >= 0.01；
- 训练期 bootstrap 总收益 5% 分位 > 0；
- 参数网格 >= 3 时，参数本身及相邻参数训练 IC > 0；端点使用最近两个参数；
- 参数网格 < 3 时不要求邻域，但必须通过其余训练门槛。

每族在合格参数中按以下顺序选择：

1. training ICIR；
2. train 中性 IC；
3. 训练 bootstrap p05；
4. 距离当前参数更近；
5. 参数值升序。

validation/recent 不参与参数选择。

## 入选复核

所选参数重新跑 5000 次 moving-block bootstrap，block 10/20/40。

硬门槛：

- coverage >= 0.90；
- train IC >= 0.01；
- train block-20 95% CI 下限 > 0；
- full 中性 IC >= 0.01；
- validation 中性 IC > 0；
- recent 中性 IC > 0；
- validation Newey-West p 值对全部 family finalists 做 BH-FDR 后 q <= 0.10；
- 训练期 plateau 通过。

block 10/40 只作敏感性：要求符号为正，不要求置信区间下限全部大于 0。避免把多个高度相关的敏感性测试重复当成独立硬门槛。

## 结论

- 所有硬门槛通过：`admitted_for_research`；
- 数据不可计算：`data_gap`，且记录原因；
- 没有合格参数：`rejected`；
- finalist 数不足或结果不完整：`inconclusive`。

## 产物

- `r535_full_family_census_preregistration.json` + sidecar；
- `r535_full_family_census_result.json` + sidecar；
- `r535_full_family_census_result.md` + sidecar；
- ResearchLedger 记录、资产台账和审计更新。

## 非目标

- 不直接把统计入选因子加入 active/watch 组合；
- 不重写历史 P1-P4 结果；
- 不把 recent 用于参数排序；
- 不生成收益或交易结论。
