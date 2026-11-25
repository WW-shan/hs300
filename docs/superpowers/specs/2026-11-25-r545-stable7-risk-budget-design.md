# R545 稳定 7 因子风险预算与动态敞口设计

## 目标

固定 R543 的 7 因子集合和 R544 的默认执行骨架：

- 10 仓
- 60 日
- 行业上限 3
- stop -12%
- T+1 开盘
- 2× 基线成本
- 无杠杆，最大敞口 100%

只研究仓位预算层：

- 固定现金比例
- 回撤 guard
- 波动率目标
- 波动率目标 + 回撤 guard

## 冻结风险预算变体

- `control`：budget_scale=1.0
- `cash_80`：budget_scale=0.8
- `cash_60`：budget_scale=0.6
- `cash_40`：budget_scale=0.4
- `dd_10`：组合回撤 <= -10% 时 budget_scale=0.5，否则 1.0
- `dd_15`：组合回撤 <= -15% 时 budget_scale=0.5，否则 1.0
- `vol_10`：目标年化波动 10%，使用候选最近 60 日等权收益，floor=0.5，最大 1.0
- `vol_12`：目标年化波动 12%，floor=0.5，最大 1.0
- `vol_12_dd_10`：同时使用 vol_12 和 dd_10，取较小 budget_scale

所有 compute 只读 `PositionSizingContext` 中的当日及以前信息。`budget_scale` 只能缩小预算，不允许放大。

## 选择窗口

只在训练期选择：

- 2022-05-05 至 2024-12-31
- 2× 成本
- annual >= 10%
- Sharpe >= 0.90
- MDD >= -20%
- trades >= 20

排序：

1. MDD 更好
2. annual 更高
3. Sharpe 更高
4. 变体固定顺序

recent 和 validation 不参与风险预算选择。

## 确认

选中变体运行：

- full 2× / 3× / 5×
- recent 2× / 3× / 5×
- validation 2×
- 6 个 fresh starts 2×
- full/recent 5000 次 block bootstrap

共 9 个训练变体 + 13 个确认运行。

## 门槛

- full annual >= 10%
- full Sharpe >= 0.90
- full MDD >= -20%
- recent total return > 0
- validation annual > 0
- 4/6 fresh starts 为正且中位数为正
- 3× full annual >= 8%
- 3× full MDD >= -22%
- 3× recent >= -3%
- 5× warning-only，不做硬否决

recent 已观察，本轮即使通过也只能 `watch_only`。

## 对照

R543 默认全敞口、R539C 13 因子 baseline、沪深300、PIT 等权。

结果必须同时报告：收益、Sharpe、MDD、预算缩放、交易数、3× 和 5× 成本。
