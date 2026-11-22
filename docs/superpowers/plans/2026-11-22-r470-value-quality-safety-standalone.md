# R470 value_quality_safety 独立 sleeve 确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 检查 R452 中统计信号很强、但 60 日边际贡献年度同号不足的 `value_quality_safety`，是否能作为独立收益 sleeve 使用；不把它已有的组合层结果当作充分证据。

**Architecture:** 新增 append-only R470 runner。复用 R464 的 `value_quality_safety` 面板和真实 T+1 回测，只对该单因子做仓数、持有期、入场/退出阈值网格；训练+验证选样，检验+近一年确认。另保留 R452 的 5/20/60 日 IC 和边际贡献作为诊断，不参与组合参数选择。

**Tech Stack:** Python 3.11+、Polars、pytest、R453 bootstrap、R464 runner。

## Global Constraints

- 因子固定为 `value_quality_safety`，方向 high。
- 只改变：仓数 `10/20`、持有期 `20/30/40`、入场 `60/70/80`、退出 `30/40/50`。
- 训练+验证选样，检验+近一年只确认。
- 2× 主成本，3× 压力，5× warning-only；无杠杆，总暴露 <= 1。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。
- 不覆盖 R452 或 R464 的已有证据。

## Tasks

1. 写 R470 预注册、sidecar 和红测。
2. 用 R464 面板构造 `value_quality_safety`，记录覆盖率。
3. 跑训练+验证网格；对前 8 个候选做验证 bootstrap，对前 4 个做局部参数邻域。
4. 冻结选中样本，跑检验、近一年、3× 和 5× warning。
5. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`，提交推送。

## Acceptance

- 所有样本为单因子、无杠杆、权重和 = 1。
- 训练+验证不触碰检验/近一年。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
- 不把 R452 的统计通过或 R464 的组合失败改写成可用策略。
