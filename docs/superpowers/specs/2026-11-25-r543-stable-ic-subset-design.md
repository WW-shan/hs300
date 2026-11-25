# R543 稳定 IC 因子子集设计

## 目标

基于 R542 的因子 IC 诊断，测试一个更简单、更稳健的因子子集：

> 仅保留在 train 和 validation 两个窗口 directional IC 都为正的因子；其余组合和执行完全冻结。

本轮只改变因子集合/权重这一层，不同时改持仓数、持有期、行业上限、止损或成本模型。

## 重要限制

R542 已观察到 recent 结果，因此 R543 的 recent 不是独立盲测。候选规则虽然只使用 train+validation（`train_ic > 0` 且 `validation_ic > 0`），但 R543 仍只能给出 `watch_only`，不能升级为 active 或 admission。

## 冻结候选

由 R542 因子 IC 规则得到 7 个因子：

- `amount_level`
- `bollinger_width`
- `dividend_yield_quantile`
- `fund_holding_ratio_absolute`
- `max_return`
- `turnover_level`
- `vol_of_vol`

排除：

- `fund_count_quantile`
- `lhb_inst_net_buy_event`
- `lhb_net_buy_holding`
- `market_down_beta`
- `repurchase_event_decay`
- `repurchase_holding`

被排除的因子均是 train 或 validation IC 非正；最近一年不参与集合筛选。

## 冻结执行

- 7 因子等权 rank composite，沿用 R531 `_core_frame` mean-available 方式；
- 10 仓、60 日、行业上限 3；
- stop=-12%，无 trailing；
- T+1 开盘成交；
- 2x 基线、3x 压力、5x warning-only；
- 无杠杆，最大敞口 100%。

## 评估窗口

- full：2022-05-05 至 2026-11-05
- validation：2025-01-01 至 2025-10-13
- recent：2025-10-14 至 2026-11-05
- fresh starts：R539 的 6 个固定起点

## 正式运行

- full 2x / 3x / 5x；
- recent 2x / 3x / 5x；
- validation 2x；
- 6 个 fresh starts 2x；
- 共 13 次正式回测，全部走 `run_with_evidence()` + `require_formal_evidence()`。

## 门槛

数据完整性、T+1、无杠杆、最大敞口 100% 必须通过。

核心研究门槛：

- full annual >= 10%
- full Sharpe >= 0.90
- full MDD >= -20%
- recent total return > 0
- validation annual > 0
- 6 个 fresh starts 中至少 4 个为正，且中位数为正
- 3x full annual >= 8%
- 3x full MDD >= -22%
- 3x recent total return >= -3%
- 5x 只做 warning，不硬否决

Sharpe 0.90 是明确为“7 因子简化子集”设定的研究观察门槛，不是生产放宽；R543 即使全部通过也只允许 `watch_only`。

## 对照

- R539C 13 因子 baseline；
- 沪深300；
- PIT 成分等权。

报告必须同时展示收益、Sharpe、MDD、交易数和换手取舍，不得只报告 recent 转正。

## 输出

- 不可变 JSON/Markdown + sidecar；
- 5,000 次 block bootstrap（full/recent）；
- ResearchLedger；
- 资产台账新版本；
- 明确 recent 已观察、非独立 OOS。
