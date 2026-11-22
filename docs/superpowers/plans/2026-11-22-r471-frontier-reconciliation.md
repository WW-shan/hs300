# R471 因子研究前沿对账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 R464-R470 的最新证据统一成一张前沿台账，明确当前最接近候选、已证伪方向、仍需继续研究的方向。

**Architecture:** append-only 汇总脚本，只读取已冻结的 R464-R470 JSON，不重新跑回测、不改参数。输出 JSON/Markdown/sidecar。

## Tasks

1. 写预注册、sidecar 和红测。
2. 汇总因子库规模、准入数、候选状态和负知识。
3. 输出前沿对账报告，验证哈希和测试。
4. 提交推送并观察 CI。

## Acceptance

- 不覆盖 R464-R470。
- 所有引用输入有 SHA-256。
- 不把拒绝候选写成可用策略。
