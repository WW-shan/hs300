# R404 核心+HS300 配对蒙特卡洛验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal

对 R403 的 70/80/90% 核心权重做配对 moving-block bootstrap，确认收益、Sharpe、回撤和 HS300 超额不是单一路径偶然；80/20 为主候选，3× 成本作为硬压力。

## Constraints

- 冻结候选 4、月度再平衡和指数代理假设。
- 5000 次配对重采样、20 日块、固定种子 404。
- 2× 主口径，3× 主候选压力；5× 不参与硬否决。
- 不生成订单、纸面目标或生产配置。
