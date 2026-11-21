# R395 因子权重蒙特卡洛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 验证 `multi_dimension_v1` 当前 8 因子等权是否只是单点配置；用预登记的训练段权重蒙特卡洛寻找稳健权重邻域，冻结后只在检验段确认一次。

**Architecture:** 复用 `scripts/research_factor_set_portfolio.py` 的真实 `StrategyBacktestService`、`open_t+1`、20 仓和无杠杆路径。只扰动 8 个 alpha 因子权重，不改变因子公式、方向、持仓数或引擎。选择只用训练段；检验段、近一年和弱窗口只做诊断。

**Tech Stack:** Python 3.11、Polars、pytest、现有 T+1 回测缓存、JSON/Markdown evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

## Global Constraints

- 8 个成员与方向冻结为 `multi_dimension_v1` v1。
- 权重必须非负、总和为 1、单腿最大 0.30、最小 0.01；无杠杆。
- 训练段固定 `2022-05-05..2025-05-05`，检验段固定 `2025-05-06..2026-11-05`。
- 选择只看训练段；检验段只评分，不回头选权。
- 2× 为主成本，3× 为压力；5× 不参与本轮否决。
- 参数邻域必须至少包含中心与单维上下扰动，不能只凭一个最优点晋升。
- 不生成订单、纸面目标或生产配置；`active_version` 保持 null。

## Task 1: 预登记与红测

**Files:**
- Create: `data/factor_research/research/r395_factor_weight_mc_preregistration.json`
- Create: `data/factor_research/research/r395_factor_weight_mc_preregistration.json.sha256`
- Create: `backend/tests/test_r395_factor_weight_mc.py`

- [ ] 写预登记：成员、方向、训练/检验窗口、权重边界、128 个拉丁超立方样本、种子 395、门槛。
- [ ] 红测断言预登记冻结、样本确定性、权重无杠杆、输出不覆盖。

## Task 2: 实现蒙特卡洛 runner

**Files:**
- Create: `scripts/research_r395_factor_weight_mc.py`

- [ ] 读取并校验 `multi_dimension_v1` v1。
- [ ] 以等权中心生成 128 个预登记权重样本，加入等权中心。
- [ ] 每个样本跑训练段 2×；按训练年化、训练 Sharpe、训练 MDD 与邻域稳定性筛选。
- [ ] 对入围样本跑检验段 2× 和训练/检验 3× 确认。
- [ ] 报告权重分布、稳健邻域、相对等权增量和最差窗口。

## Task 3: 验证与发布

- [ ] 定向测试通过。
- [ ] Ruff、`git diff --check`。
- [ ] JSON/Markdown/sidecar 与 runner 哈希绑定。
- [ ] 提交并推送，观察 CI 与 Docker。
