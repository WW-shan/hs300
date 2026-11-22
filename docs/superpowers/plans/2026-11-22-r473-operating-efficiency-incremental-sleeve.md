# R473 营运效率增量 sleeve 验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 验证 R468 的 `inventory_growth_minus_cogs_growth` 作为独立 sleeve 加入 R463 四因子价值质量组合后，是否提供真实边际收益，而不只是单独回测好看。

**Architecture:** 新增 append-only R473 runner。固定 R468 选中参数，分别构造四因子基线、四因子+营运效率等权组合、以及按 10%/20%/30% 权重加入的组合。训练+验证只用于选择权重和组合参数，检验+近一年确认。另做去除营运效率后的对照和集中度归因。

**Tech Stack:** Python 3.11+、pytest、R453 bootstrap、R468/R464 runner。

## Global Constraints

- 营运效率因子固定为 `inventory_growth_minus_cogs_growth`，方向 low，参数固定 R468 的 10 仓/20 日/入场 70/退出 30。
- 四因子基线固定为 R463 的四个双准入因子，方向 high。
- 组合权重网格：营运效率 sleeve 10%/20%/30%，其余 90%/80%/70% 在四因子内等权。
- 训练+验证选样；检验+近一年确认；2× 主成本、3× 压力、5× warning；无杠杆。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。
- 不覆盖 R468/R469/R472。

## Tasks

1. 写 R473 预注册、sidecar 和红测。
2. 生成确定性权重样本，并构造四因子与营运效率 sleeve 曲线。
3. 训练+验证筛选；对候选做验证 bootstrap 和权重邻域检查。
4. 冻结选中组合后跑检验、近一年、3× 和 5× warning；输出增量归因。
5. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`，提交推送。

## Acceptance

- 所有样本权重和 = 1、无杠杆。
- 训练+验证不触碰检验/近一年。
- 增量只以同口径四因子对照为准，不拿孤立收益替代边际贡献。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
