# R393 财务费用结构与现金质量因子 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在现有 PIT 财务快照上新增 6 个不与 R82/R242/R383/R384 重复的费用结构、现金质量和资产负债表安全因子，并完成 P7/P6、统计和组合验证。

**Architecture:** 扩展 `app/backtest/fundamentals.py` 的四表 PIT 派生列，复用独立公告日依赖；新增注册表条目和 P7 审计；不改变既有财务因子公式。研究阶段沿用 P5/P8 的中性化 IC、增量、组合和滚动一年。

**Tech Stack:** Python 3.11、Polars、pytest、现有 Factor Research P5/P6/P7/P8。

**Spec:** `docs/superpowers/plans/2026-11-21-financial-operating-efficiency-r383.md` 与 `...r384.md` 的 PIT 约束。

## Global Constraints

- 只新增 6 个因子：`sales_expense_ratio_ttm_low`、`management_expense_ratio_ttm_low`、`ocf_to_revenue_ttm`、`fcf_proxy_to_revenue_ttm`、`cash_to_assets_latest`、`payables_to_cogs_ttm`。
- 不重复 R82/R242/R383/R384 已研究公式。
- 财务 PIT：事件日当天为空，严格下一可观测日生效；不补零、不插值、不跨季度缺口硬拼。
- 因子方向：费用率、应付款/成本为 `low`；OCF/营收、FCF/营收、现金/资产为 `high`。
- 金融行业不参与资产负债表安全类组合，避免口径错配。
- 无杠杆；2× 主成本，3× 压力，5× 仅 warning。
- 不生成订单、纸面目标或生产配置。

## Task 1: 预登记

- [ ] 创建 `data/research/r393_financial_structure_preregistration.json` 和 sidecar。
- [ ] 冻结 6 个公式、方向、主期限 60 日、样本和门槛。
- [ ] 写红测，确认新因子尚未注册。

## Task 2: 实现派生列

**Files:**
- Modify: `backend/app/backtest/fundamentals.py`
- Modify: `backend/app/factors/registry.py`
- Modify: `backend/app/factor_research/financial_pit.py`
- Test: `backend/tests/test_fundamental_derived.py`

- [ ] `sales_expense_ratio_ttm_low = TTM selling_expenses / TTM revenue`
- [ ] `management_expense_ratio_ttm_low = TTM management_expenses / TTM revenue`
- [ ] `ocf_to_revenue_ttm = TTM OCF / TTM revenue`
- [ ] `fcf_proxy_to_revenue_ttm = (TTM OCF - TTM capex) / TTM revenue`
  - capex 不可直接从现有字段稳定推导时，本轮只使用投资现金流代理并记录限制；不得伪造 capex。
- [ ] `cash_to_assets_latest = cash_and_equivalents / total_assets`
- [ ] `payables_to_cogs_ttm = accounts_payable / TTM operating_costs`
- [ ] 每个因子的公告日依赖：
  - 费用率：income
  - OCF/营收：cash_flow + income + metrics
  - FCF/营收：cash_flow + income
  - 现金/资产：balance_sheet
  - 应付款/成本：balance_sheet + income
- [ ] 分母 <= 0 或数据缺失时 null。
- [ ] 季度缺口、非精确 t-1/t-4 时 null。

## Task 3: P7/P6 审计

- [ ] P7 必须验证 6 个因子在公告日 T+1 生效。
- [ ] P6 必须标记 scan-ready/portfolio-ready。
- [ ] 不修改 R383/R384 冻结 ID。

## Task 4: 统计与组合

- [ ] 复用 P8 的中性化 IC、边际 IC、BH-FDR。
- [ ] 只有统计通过候选进入组合替换筛查。
- [ ] 2×/3× 硬门槛，5× warning，无杠杆。

## Task 5: 验证与提交

- [ ] 定向测试、全量后端测试、Ruff、`git diff --check`。
- [ ] 提交并推送，观察 CI + Docker。
