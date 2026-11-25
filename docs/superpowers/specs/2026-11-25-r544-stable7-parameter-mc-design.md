# R544 稳定 7 因子参数鲁棒性与 Monte Carlo 设计

## 目标

固定 R543 的 7 因子集合，只优化执行参数：

- 持仓数：10 / 15 / 20
- 最长持有：40 / 60 / 80 日
- 行业上限：2 / 3 / 4
- 止损：-8% / -10% / -12% / -15%

共 108 组。

本轮不增加、不删除因子，不改评分方向，不改 T+1、无杠杆和最大敞口 100%。

## 数据与来源

- R543 7 因子 stable-IC 子集；
- R542 冻结 runtime 和 factor panel；
- R526 PIT 成员与行业；
- 所有组合收益通过项目 `run_with_evidence()` 和 `require_formal_evidence()`。

## 选择窗口

参数选择只使用训练期：

- 2022-05-05 至 2024-12-31；
- 2× 成本；
- 无 recent、无 validation 参与参数选择。

recent 和 validation 只用于冻结参数后的确认。

## 参数网格

`3 × 3 × 3 × 4 = 108` 个训练期正式回测。

## 训练资格

单组参数需满足：

- annual >= 10%
- Sharpe >= 0.90
- MDD >= -20%
- trades >= 20

不满足的组合只保留为负知识，不进入平台选择。

## 邻域平台选择

对每个合格参数点，检查每个维度相邻一档的邻居：

- 持仓数：相邻 10 / 15 / 20
- 持有期：相邻 40 / 60 / 80
- 行业上限：相邻 2 / 3 / 4
- 止损：相邻 -8 / -10 / -12 / -15

计算邻居：

- annual >= 8% 且 Sharpe >= 0.85 且 MDD >= -22% 的通过率
- 邻居 annual 中位数
- 邻居 Sharpe 中位数
- 邻居 MDD 中位数

排序优先级：

1. 邻居通过率高
2. 邻居 Sharpe 中位数高
3. 邻居 annual 中位数高
4. 自身 MDD 较好
5. 距离默认参数更近

取前 5 个不同参数点进入 Monte Carlo。

## Monte Carlo

对前 5 个平台点，用训练期净值做：

- block size 20
- 5000 次 bootstrap
- seed 544

最终选择：

1. bootstrap total p05 高
2. 邻居 Sharpe 中位数高
3. 邻居 annual 中位数高
4. 距默认参数更近

## 冻结确认

选中参数后运行：

- full 2× / 3× / 5×
- recent 2× / 3× / 5×
- validation 2×
- 6 个 fresh starts 2×

共 13 次正式确认回测。

## 门槛

核心：

- full annual >= 10%
- full Sharpe >= 0.90
- full MDD >= -20%
- recent total return > 0
- validation annual > 0
- 6 个 fresh starts 至少 4 个为正且中位数为正

3× 压力：

- full annual >= 8%
- full MDD >= -22%
- recent >= -3%

5× 只做 warning，不硬否决。

由于 recent 已反复观察，本轮即使全部通过也只能 `watch_only`，不能 active。

## 输出

- 108 个训练网格结果；
- 前 5 个平台点和 bootstrap；
- 选中参数的 full/recent/validation/fresh/3×/5×；
- 与 R543 默认参数（10/60/cap3/stop-12）对照；
- 不可变 JSON/Markdown/sidecar、ResearchLedger、v38 asset ledger。
