# R546 vol_12 风险预算确认设计

## 目标

只确认 R545 训练表中未选中的 `vol_12` 风险预算：

- 固定 R543 稳定 7 因子；
- 10 仓、60 日、行业上限 3、stop -12%；
- T+1 开盘、无杠杆、最大敞口 100%。

不增加因子，不调执行参数，不搜索新风险预算。

## 重要限制

R545 已经观察过 recent，R546 的 recent 不是独立 OOS。
R546 即使通过全部门槛，也只能给出 `watch_only`，不能升级为 active、订单或纸面目标。

## 变体

`vol_12`：

- 候选最近 60 日等权收益年化波动；
- budget_scale = min(1, 12% / realized_vol)；
- floor 0.5；
- 只缩小预算，不放大。

## 正式确认

- full 2× / 3× / 5×
- recent 2× / 3× / 5×
- validation 2×
- 6 个 R539 固定 fresh starts 2×
- full/recent 2× 各 5000 次 block bootstrap

共 13 次正式运行。

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

## 对照

- R543 稳定 7 因子全敞口；
- R545 vol_10；
- 沪深300；
- PIT 成分等权。

报告必须展示收益、Sharpe、MDD、平均敞口、成本敏感性和 bootstrap。
