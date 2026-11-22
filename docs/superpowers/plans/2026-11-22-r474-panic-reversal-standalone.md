# R474 恐慌反转情绪 sleeve 独立确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 检验 R416 统计通过但只在 R417“替换一条腿”中失败的 `new_panic_reversal_5d`，是否能作为独立情绪 sleeve 使用，而不是直接塞进旧组合。

**Architecture:** 新增 append-only R474 runner。复用 R416 的公式和 R464/R468 的真实 T+1 回测；对 5/10/20 日回看、量比阈值、仓位、持有期和阈值做小网格。训练+验证选样，检验+近一年确认。另做集中度和交易归因。

**Tech Stack:** Python 3.11+、Polars、pytest、R453 bootstrap、R468 runner。

## Global Constraints

- 情绪因子固定为 `new_panic_reversal_5d`，方向 high；只允许回看 5/10/20 日和量比阈值 1.2/1.5/2.0。
- 仓数 10/20，持有期 5/10/20，入场 60/70/80，退出 30/40/50。
- 训练+验证选样，检验+近一年确认；2× 主成本，3× 压力，5× warning；无杠杆。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。
- 不覆盖 R416/R417 的已有证据。

## Tasks

1. 写 R474 预注册、sidecar 和红测。
2. 构造情绪因子面板并生成确定性样本。
3. 跑训练+验证筛选；对前 8 个候选做验证 bootstrap，对前 4 个做参数邻域。
4. 冻结选中样本，跑检验、近一年、3× 和 5× warning；输出集中度归因。
5. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`，提交推送。

## Acceptance

- 所有样本为单因子、无杠杆、权重和 = 1。
- 训练+验证不触碰检验/近一年。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
