# R466 因子袖风险预算联合蒙特卡洛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 R464 联合因子组合和 R465 尾部诊断之后，检验 sleeve 层风险预算能否改善尾部，而不是继续在单只股票组合上调权重。

**Architecture:** 新增 append-only R466 runner。复用 R464 的因子面板和真实 T+1 单因子回测，先生成每个 sleeve 的日收益曲线；再用只依赖过去收益的等权、逆波动、ERC、最小方差规则组合 sleeve。训练+验证用于选择风险预算参数，检验+近一年只做确认。所有规则都无杠杆、总权重 <= 1、2× 主成本、3× 压力、5× warning-only。

**Tech Stack:** Python 3.11+、Polars、NumPy、pytest、R464 runner、R453 moving-block bootstrap。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

## Global Constraints

- sleeve 固定为 R464 已使用的九个单因子袖：
  - 现金流稳定性
  - 股息率
  - 基金持仓低位
  - 前十大股东持股变化
  - 40 日残差低波
  - 融资余额变化
  - OCF margin
  - 波动率的波动
  - value_quality
- universe 固定为：
  - `core5`: 四因子 + value_quality
  - `seven_vq`: R464 七因子 + value_quality
  - `compact4`: 现金流稳定性 + 股息率 + 基金持仓低位 + value_quality
  - `extended9`: 九个 sleeve 全用
- 风险预算方法固定为等权、逆波动、ERC、最小方差。
- 动态规则只使用当前日期之前的收益；不允许使用当天或未来收益估权。
- lookback 固定为 20/60/120 个交易日；再平衡固定为 20/60 个交易日；权重上限固定为 0.25/0.40；权重下限 0.02。
- 组合层换手成本 5bp 单边；不另加杠杆或现金收益。
- 训练 `2022-05-05..2024-05-05`；验证 `2024-05-06..2025-05-05`；检验 `2025-05-06..2026-11-05`；近一年 `2025-10-14..2026-11-05`。
- 2× 主成本，3× 压力；5× 只记录 warning，不硬否决。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 `null`。

## Tasks

1. 写预注册 JSON、sidecar 和红测；冻结 universe、风险预算方法、参数网格和门槛。
2. 用 R464 面板生成九个单因子 sleeve 的 2022-05-05..2026-11-05 真实 T+1 曲线；缺失或空曲线 fail-closed。
3. 实现只依赖历史收益的权重估计、换手成本和组合曲线；生成确定性参数样本。
4. 训练+验证筛选；对前 12 个候选做验证段 bootstrap，对前 4 个做单轴邻域审计。
5. 冻结选中规则后跑检验、近一年、3× 和 5× warning；输出 JSON/Markdown/sidecar。
6. 运行定向测试、后端全量测试、Ruff、`git diff --check`；更新台账并提交推送。

## Acceptance

- 每个 sleeve 曲线都有非空日期和正交易日数。
- 权重和 = 1，权重上限满足，所有样本无杠杆。
- 动态权重只由 `date < current_date` 的收益计算。
- 选中规则在训练+验证后冻结；检验/近一年不参与选样。
- 只有全部门槛通过才是 `watch_candidate`；否则记录负知识并拒绝。
- 5× 只做诊断，不作为硬门槛。
