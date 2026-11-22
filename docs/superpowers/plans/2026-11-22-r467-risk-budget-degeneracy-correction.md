# R467 风险预算退化修正与重新确认 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正 R466 中 `compact4 + max_weight=0.25` 导致四种风险预算方法退化为等权的问题，在不覆盖 R466 原始证据的前提下重新做预注册确认，判断 sleeve 层风险预算是否真的有增量。

**Architecture:** 新增 append-only R467 runner，复用 R466 的 sleeve 曲线和组合器。只保留动态风险预算能够产生非退化权重的样本：`max_weight > 1/N`，且训练+验证期间估计权重确实随历史收益变化。另加等权控制组，但等权不参与动态方法排名。训练+验证选样，检验+近一年确认；5× 只做 warning。

**Tech Stack:** Python 3.11+、NumPy、pytest、R466 runner、R453 moving-block bootstrap。

## Global Constraints

- 不覆盖 R466 的 JSON/Markdown/sidecar。
- 修正规则只针对 R466 的 sleeve 层组合器，不改变单因子回测和 R464 原始结果。
- 动态方法必须满足 `max_weight > 1 / len(sleeve_names) + 1e-12`。
- 动态方法在训练+验证期间必须至少有 3 次不同的权重向量；否则标记 `degenerate_weight_path`，不能参与选样。
- 等权控制组单独保留，但不能被标成 ERC/逆波动/最小方差的增量证据。
- 训练+验证选样；检验+近一年只确认；2× 主成本、3× 压力、5× warning-only；无杠杆。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。

## Tasks

1. 写 R467 预注册 JSON、sidecar 和红测，冻结退化判定规则。
2. 实现动态权重路径审计，记录不同权重向量数量和最大权重变化。
3. 排除退化样本，重新跑训练+验证；对前 12 个非退化候选做验证 bootstrap，对前 4 个做局部参数邻域。
4. 冻结选中规则，跑检验、近一年、3× 和 5× warning。
5. 输出 JSON/Markdown/sidecar，跑定向测试、全量测试、Ruff、`git diff --check`，提交推送。

## Acceptance

- R466 原始证据不被修改。
- 所有参与动态排名的候选满足权重上限和 `max_weight > 1/N`。
- 至少 3 个不同权重向量；否则明确标记退化并排除。
- 等权控制组与动态方法分开报告。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
