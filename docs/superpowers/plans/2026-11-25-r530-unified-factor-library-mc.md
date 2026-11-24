# R530 统一 PIT 因子库蒙特卡洛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 冻结并复核历史因子库 15 个推荐参数，在统一 PIT 名单/行业/收益口径下生成全库统计能力矩阵与严格蒙特卡洛结论。

**Architecture:** 新增 `scripts/research_r530_unified_factor_library_mc.py`，导入现有 P1–P4 评估器和共享参数 MC 工具；只复用历史冻结推荐参数，不重新搜索。通过 R526 bundle 锁定名单与行业，使用项目本地价格/扩展数据，结果经研究合同 helper 发布。

**Tech Stack:** Python 3.11+/3.12、Polars、现有评估器、`ResearchLedger`、研究合同、pytest、Ruff。

**Spec:** `docs/superpowers/specs/2026-11-25-r530-unified-factor-library-mc-design.md`

## Global Constraints

- `RESEARCH_TYPE = "statistical_screen"`。
- 不新增、不替换、不重新选择参数；候选数必须为 15。
- 统一使用 R526 daily membership 和 industry history。
- 2× 成本不是本轮统计 IC 的计算内容；本轮不产生组合收益。
- 最终研究合同类型为 `statistical_screen`，不得输出 `project_formal`、订单、纸面目标或 `active_version`。
- 所有输出使用合同 helper，结果不可覆盖，sidecar/ledger 必须一致。

### Task 1: 合同化 runner 声明与候选冻结测试

**Files:**
- Create: `scripts/research_r530_unified_factor_library_mc.py`
- Create: `backend/tests/test_r530_unified_factor_library_mc.py`

- [ ] **Step 1: Write failing tests**

Test:
- all four source MC files exist and sidecars verify;
- candidate discovery returns exactly 15;
- each returned parameter equals its source `recommended_parameter`;
- no candidate is re-selected from result IC;
- runner declares `RESEARCH_TYPE="statistical_screen"` and a preregistration path.

- [ ] **Step 2: Run tests and confirm failure**

```bash
cd backend && uv run --frozen pytest tests/test_r530_unified_factor_library_mc.py -q
```

- [ ] **Step 3: Implement candidate loading functions**

Implement pure functions:
- `load_recommended_candidates(root: Path) -> list[Candidate]`;
- `load_pit_membership(root: Path) -> pl.DataFrame`;
- `load_pit_industry(root: Path) -> pl.DataFrame`.

No result or prereg file is written in this task.

- [ ] **Step 4: Re-run tests**

### Task 2: 实现统一 PIT 评估器

**Files:**
- Modify: `scripts/research_r530_unified_factor_library_mc.py`
- Modify: `backend/tests/test_r530_unified_factor_library_mc.py`

- [ ] **Step 1: Write failing tests for membership filtering and as-of industry mapping**

Test:
- membership sessions each contain exactly 300 symbols;
- a symbol/date not in daily membership never enters the joined factor frame;
- industry mapping uses the latest effective date not later than the evaluation date.

- [ ] **Step 2: Implement full price panel loader and factor candidate evaluators**

Support P1/P2/P3/P4 source families by importing their existing evaluators. Build full history before membership filtering. Forward return is `close.shift(-5)/close-1`.

- [ ] **Step 3: Implement plateau neighbor evaluation**

For a frozen candidate, evaluate the same family’s grid or the two adjacent points around the frozen parameter using the same PIT membership and returns; do not re-select a parameter.

### Task 3: 蒙特卡洛和 FDR

**Files:**
- Modify: `scripts/research_r530_unified_factor_library_mc.py`
- Modify: `backend/tests/test_r530_unified_factor_library_mc.py`

- [ ] **Step 1: Write failing tests for deterministic bootstrap**

Test same seed returns identical CI; different seed returns distinct output; all 10/20/40 block results exist.

- [ ] **Step 2: Implement**
  - full/train/validation/recent IC blocks;
  - 5000 block bootstrap for blocks 10/20/40;
  - BH-FDR across all 15 candidate full p-values;
  - plateau validation;
  - gate evaluation.

- [ ] **Step 3: Run tests and verify fixed-seed replay**

### Task 4: 运行冻结研究并发布 evidence

**Files:**
- Create: `data/factor_research/research/r530_unified_factor_library_mc_preregistration.json`
- Create: `.sha256`
- Create: `data/factor_research/research/r530_unified_factor_library_mc_result.json`
- Create: `.sha256`
- Create: `data/factor_research/research/r530_unified_factor_library_mc_result.md`
- Create: `.sha256`

- [ ] **Step 1: Generate preregistration from final runner hash**

Include source files, bundle hashes, 15 frozen candidates, seeds, windows, gates, and output policy.

- [ ] **Step 2: Run runner once; it refuses to overwrite**

Use `publish_research_artifacts()` and the append-only ledger.

- [ ] **Step 3: Verify all candidates have one conclusion and no execution outputs**

### Task 5: 全量验证、CI、提交**

- [ ] Run contract audit, conformance audit, asset audit.
- [ ] Run focused tests and full backend tests.
- [ ] Run frontend build.
- [ ] Commit and push with DCO.
- [ ] Wait for CI and Docker success.
