# R403 核心 + HS300 Beta 补全 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal
验证静态混合候选 4 与沪深300指数能否改善最近一年相对表现，同时保持长期收益和回撤。

## Constraints
- 候选 4 冻结；只加 10/20/30/40% 沪深300 beta，总权重不超过 1。
- 月频再平衡；股票 2× 成本，指数单独扣管理费和换手滑点。
- 训练、验证、盲测分开；不生成订单或生产配置。
