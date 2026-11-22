# R468 营运效率因子独立 sleeve 确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 确认 R384 两个统计/增量通过但组合替换失败的营运效率因子，是否具备作为独立收益 sleeve 的资格，而不是只能在旧 8 腿组合里做一次替换。

**Architecture:** 新增 append-only R468 runner。复用 R453 的真实 T+1 矩阵回测和 R384 的 PIT 财务面板，分别回测 `inventory_turnover_improvement_yoy` 与 `inventory_growth_minus_cogs_growth` 的单因子组合，并对仓数、持有期、入场/退出阈值做预注册小网格。训练+验证选样，检验+近一年确认；不覆盖 R384。

**Tech Stack:** Python 3.11+、Polars、pytest、R453 bootstrap、R464 面板构造。

## Global Constraints

- 因子固定为：
  - `inventory_turnover_improvement_yoy`，方向 high
  - `inventory_growth_minus_cogs_growth`，方向 low
- 单因子 sleeve，不用其他因子加权；每个样本只属于一个因子。
- 仓数固定 `10/20`，持有期 `20/30/40`，入场阈值 `60/70/80`，退出阈值 `30/40/50`。
- 训练 `2022-05-05..2024-05-05`；验证 `2024-05-06..2025-05-05`；检验 `2025-05-06..2026-11-05`；近一年 `2025-10-14..2026-11-05`。
- 2× 主成本，3× 压力；5× 仅 warning；无杠杆、总暴露 <= 1。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。

## Tasks

1. 写 R468 预注册、sidecar 和红测，冻结因子、方向、网格与门槛。
2. 构造 PIT 财务面板并生成确定性参数样本。
3. 训练+验证筛选；对前 8 个候选做验证 bootstrap，对前 4 个做局部参数邻域。
4. 冻结选中样本，跑检验、近一年、3× 和 5× warning。
5. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`，提交推送。

## Acceptance

- 两个因子的 PIT 值存在，非空覆盖和首值日被记录。
- 所有样本为单因子、无杠杆、权重和 = 1。
- 训练+验证不触碰检验/近一年；检验/近一年只确认。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
- 不把 R384 的组合替换失败改写成成功。
