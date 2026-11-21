# R397 因子子集与组合参数联合蒙特卡洛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal

在不新增因子公式的前提下，检验 R395/R396 暴露出的“少腿组合可能更强”假设：预登记 3–7 腿因子子集和 10/20/30 仓、20/30/40 日持有期，按训练段选稳健邻域，检验段只确认。

## Constraints

- 因子集合固定为 `multi_dimension_v1` 的 8 个 watch 成员。
- 只改变成员是否参与、等权权重、持仓数和持有期；不改变因子公式和方向。
- 所有组合无杠杆，最大总仓位 <= 1.0。
- 2× 主成本，3× 压力；5× 不参与硬否决。
- 训练 `2022-05-05..2025-05-05`，检验 `2025-05-06..2026-11-05`。
- 不生成订单、纸面目标或生产配置；active_version 保持 null。

## Tasks

1. 冻结因子子集、组合参数网格和门槛；写红测。
2. 跑训练段组合网格，记录每个子集/参数点的收益、Sharpe、MDD。
3. 选取稳健邻域，冻结后在检验段确认。
4. 输出 JSON/Markdown/sidecar、定向测试、Ruff、`git diff --check`。
5. 提交并推送，观察 CI 与 Docker。
