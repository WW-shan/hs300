# R464 因子库联合蒙特卡洛与价值质量复合验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不新增原始数据源的前提下，把当前已准入因子、R452 价值质量复合因子和 R463 近候选放进同一套预注册联合蒙特卡洛，验证因子袖组合与组合参数是否同时稳健，重点检查近一年尾部风险能否改善。

**Architecture:** 新增 append-only R464 runner。复用 R453 的真实 T+1 矩阵回测、R452/R455 的 `value_quality`/`value_quality_safety` 构造和 moving-block bootstrap。训练+验证段只用于抽样筛选与邻域稳健性判断；检验+近一年段只做一次性确认，不参与选样。所有结果写入 JSON/Markdown/sidecar，并由定向测试冻结关键口径。

**Tech Stack:** Python 3.11+、Polars、pytest、现有 factor-rank 矩阵策略、R453 bootstrap 实现。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

## Global Constraints

- 不新增原始数据源；只组合已注册/已审计因子和已准入复合因子。
- 因子袖固定为：
  - `four_vq`: 四个双准入因子 + `value_quality`
  - `seven_vq`: R397 id26 七因子 + `value_quality`
  - `four_vqs`: 四个双准入因子 + `value_quality_safety`
  - `three_vq`: 现金流稳定性 + 股息率 + 低基金持仓 + `value_quality`
- 联合参数：价值质量权重、仓数、持有期、总暴露、入场阈值、退出阈值。
- 训练 `2022-05-05..2024-05-05`；验证 `2024-05-06..2025-05-05`；检验 `2025-05-06..2026-11-05`；近一年 `2025-10-14..2026-11-05`。
- 2× 主成本，3× 压力；5× 只记录 warning，不硬否决。
- 无杠杆，最大总暴露 <= 1.0。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。

## Tasks

1. 冻结因子袖、参数空间、抽样种子、训练/验证选择规则、检验/近一年确认门槛；写红测。
2. 构造 `value_quality` 与 `value_quality_safety`，生成确定性 Latin Hypercube 联合样本，并保留 R463 基线作为控制点。
3. 只跑训练+验证；对前 12 个合格样本做验证段 bootstrap，对前 4 个做单轴参数邻域审计。
4. 冻结选中样本后，一次性跑检验、近一年、3× 成本和 5× warning；输出 JSON/Markdown/sidecar。
5. 运行定向测试、后端全量测试、Ruff、`git diff --check`；更新研究台账并提交推送。

## Acceptance

- 预注册、输出和 Markdown 都有 SHA-256 sidecar。
- 样本生成可复现，且每个样本权重和 <= 1.0、最大暴露 <= 1.0。
- 检验/近一年不参与选样；测试明确断言这一点。
- 选中样本只有在训练、验证、检验、近一年、3× 压力、检验/近一年 bootstrap 尾部全部通过时才是 `watch_candidate`；否则保留负知识并拒绝。
- 不因 5× 诊断结果硬否决策略。
