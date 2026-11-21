# R396 权重稳定性审计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 对 R395 入选权重做冻结后敏感性审计，区分稳健权重区间与单点异常。

**Architecture:** 不重新选权。以 R395 报告绑定的权重和结论为输入，预登记权重扰动、留一因子和随机邻域，重跑真实 T+1 回测，检查结论是否连续。

**Tech Stack:** Python 3.11、pytest、现有 R395 runner、JSON/Markdown evidence。

## Global Constraints

- 不修改 R395 结果或预登记。
- 不根据 R396 重新选择 R395 权重。
- 20 仓、无杠杆、2× 主成本、3× 压力；5× 不参与硬否决。
- 训练 `2022-05-05..2025-05-05`；检验 `2025-05-06..2026-11-05`。
- 输出只允许 `weight_stability_complete_no_promotion`。

## Task 1: 预登记

- [ ] 冻结 R395 selected weights、等权中心、邻域定义和稳定性门槛。
- [ ] 写红测。

## Task 2: 邻域审计

- [ ] 每个因子权重 ±20% 后重新归一，其他七个保持比例。
- [ ] 每次移除一个因子，剩余权重重新归一。
- [ ] 比较 2× 训练/检验年化、Sharpe、MDD 和 3× 检验年化。
- [ ] 计算连续邻域通过率和最差变化。

## Task 3: 结论、验证、提交

- [ ] 只有邻域连续通过才标记 `stable_weight_region`。
- [ ] JSON/Markdown/sidecar、定向测试、Ruff、`git diff --check`。
- [ ] 提交并推送，观察 CI 与 Docker。
