# R535 Full Family Census Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对 85 个参数族和 472 个参数点做训练期参数选择、validation/recent 复核和 5000 次 MC。

**Architecture:** 复用 R530 的 R526 上下文、factor frame、neutral IC 和 bootstrap 原语；新增全 registry 枚举、training-only plateau/ICIR 选择和 family finalist 发布。

**Tech Stack:** Python 3.11、Polars、R530 statistical helpers、BH-FDR、ResearchLedger、Pytest。

**Spec:** `docs/superpowers/specs/2026-11-25-r535-full-family-census-design.md`

---

### Task 1: 全 registry 枚举

**Files:**
- Create: `backend/tests/test_r535_full_family_census.py`
- Create: `scripts/research_r535_full_family_census.py`

- [ ] 写失败测试，断言 62 窗口族 + 23 阈值族 = 85 族、472 参数点。
- [ ] 从 P1-P4 source 结果建立 family -> tier 映射。
- [ ] 构造每族每参数 Candidate。
- [ ] 运行聚焦测试通过。

### Task 2: 训练期选择和 plateau

**Files:**
- Modify: `scripts/research_r535_full_family_census.py`
- Test: `backend/tests/test_r535_full_family_census.py`

- [ ] 写失败测试覆盖 training-only plateau、ICIR 排序和近邻规则。
- [ ] 计算每参数训练 IC、ICIR、coverage 和 1000 次 block-20 bootstrap。
- [ ] 只按训练期选择每族一个参数。

### Task 3: 5000 次复核与 FDR

**Files:**
- Modify: `scripts/research_r535_full_family_census.py`
- Test: `backend/tests/test_r535_full_family_census.py`

- [ ] 写失败测试覆盖最终 gate 和 block 10/20/40 敏感性口径。
- [ ] 对 finalists 跑 5000 次 block 10/20/40。
- [ ] 对 validation p 值做 BH-FDR。
- [ ] 输出 admitted/rejected/data_gap/inconclusive。

### Task 4: 发布和审计

- [ ] 预注册 registry/PIT/runner hashes、窗口、seed 和门槛。
- [ ] 不可覆盖发布 result/Markdown 和 ResearchLedger。
- [ ] 更新 v27 asset ledger、conformance/contract/hygiene。

### Task 5: 验证与提交

- [ ] Ruff、聚焦测试、全部 research audits、`git diff --check`。
- [ ] 后端全量测试和前端构建。
- [ ] DCO 提交、推送、CI/Docker 轮询。
