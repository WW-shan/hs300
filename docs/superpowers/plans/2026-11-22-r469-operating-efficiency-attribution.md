# R469 营运效率 sleeve 收益归因 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对 R468 中检验段年化 37.81% 的 `inventory_growth_minus_cogs_growth` sleeve 做描述性归因，确认高收益来自稳定多笔交易，还是少数股票/月份/交易日，避免把集中收益误当可用 alpha。

**Architecture:** 新增 append-only R469 runner。复用 R468 的选中参数和真实 T+1 回测，保存完整 trades、逐月收益、按股票/月份/交易日的收益贡献和去除 top-N 交易后的曲线。只做描述性归因，不重新选参、不改 R468 的拒绝结论。

**Tech Stack:** Python 3.11+、pytest、R468 runner、R453 bootstrap。

## Global Constraints

- 源结果固定为 R468 `inventory_growth_minus_cogs_growth|p10|h20|e70|x30`。
- 不重新选因子、不重新选参数、不改变成本或窗口。
- 计算：月度收益、负月数、最差月、股票贡献、交易贡献、去除 top1/top5/top10 盈利交易后的收益。
- 2× 为主口径；3×/5× 只做已有证据对照。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。

## Tasks

1. 写 R469 预注册、sidecar 和红测。
2. 复用 R468 回测，导出 trades 和测试/近一年曲线。
3. 计算股票、月份、交易和 top-N 去除归因。
4. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`。
5. 提交并推送；观察 CI/Docker。

## Acceptance

- 归因使用 R468 冻结参数和同一成本口径。
- 输出交易总数、盈利/亏损交易数、top-N 盈利贡献和去除后的收益。
- 明确区分“描述性归因”与“可用策略”。
- 不覆盖 R468；不产生订单或生产配置。
