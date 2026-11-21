# R407 行业集中度上限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Goal
检查 2026-06 的回撤是否由低波/高股息因子的行业集中触发，并验证单行业 2/3 仓上限能否改善下尾。

## Constraints
- 只改选股上限，不改因子公式和 80/20 比例。
- 行业映射使用当前同花顺一级行业，明确记录历史 vintage 缺口。
- 只跑测试窗口和最近一年，作为定向诊断；不回头改已冻结的 R403/R404。
