# R476 因子集扩容与复合层验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 R402 的 8 腿上限，验证把新增候选因子加入现有组合后是否产生真实边际收益。

**Architecture:** 用日常 rank composite 作为组合层，不再让 `factor_rank_research` 的 8 参数上限阻断扩容。固定基线 8 腿，候选逐个加入，权重网格 10%/20%/30%；训练+验证选样，检验+近一年确认。

## Acceptance

- 组合权重和 = 1、无杠杆。
- 新增因子通过真实 T+1、3× 压力和 bootstrap。
- 不覆盖 R402；不生成订单或生产配置。
