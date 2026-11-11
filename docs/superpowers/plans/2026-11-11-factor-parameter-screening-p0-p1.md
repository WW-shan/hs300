# 因子筛选与参数蒙特卡洛 P0-P1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 建立 223 个因子的参数族分类报告，并完成现有 8 个价格风险族的统一参数搜索、训练/检验和两阶段蒙特卡洛验证。

**Architecture:** P0 新增严格校验的参数族注册表和 fail-closed 分类器；P1 复用现有 `research_alt_factor_parameters.py` 的向量化评估，将硬编码家族改为注册表驱动，并把初筛 1000 次、决赛 5000 次及 block-size 敏感性写入同一不可变证据。

**Tech Stack:** Python 3.11、Pydantic v2、Polars、NumPy、pytest、ResearchLedger、markdown/JSON evidence。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

## Global Constraints

- 不产生 active 版本、订单或纸面目标。
- 不修改历史因子 ID 的经济含义。
- 参数网格预先固定，禁止根据结果动态补点。
- 所有研究产物使用仓库相对路径，不写机器绝对路径。
- 不使用未来数据、snapshot 扩展数据或 audit unknown 字段。
- 所有新行为先写失败测试，再写最小实现。
- 中途不提交用户原有的 R359-R371、`.gitignore`、周报和 custom signals 改动。

---

### Task 1: 参数族注册表与分类模型

**Files:**
- Create: `backend/app/factor_research/parameters.py`
- Test: `backend/tests/factor_research/test_parameters.py`

**Interfaces:**
- Consumes: catalog snapshot `FactorRecord` list and parameter registry JSON.
- Produces:
  - `ParameterFamily`
  - `ParameterFamilyRegistry`
  - `FactorParameterClassification`
  - `load_parameter_registry(data_dir: Path) -> ParameterFamilyRegistry`
  - `validate_parameter_registry(registry, factor_ids: set[str]) -> list[str]`
  - `classify_factor_parameters(factors, registry) -> list[FactorParameterClassification]`

- [x] **Step 1: Write failing tests for valid and invalid registries**

```python
def test_registry_rejects_duplicate_member_and_bad_grid(tmp_path: Path) -> None:
    registry = ParameterFamilyRegistry.model_validate({...})
    errors = validate_parameter_registry(registry, {"f1", "f2"})
    assert any("duplicate member" in error for error in errors)
    assert any("ascending" in error for error in errors)
```

- [x] **Step 2: Run test and verify missing module failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py -q`

Expected: collection fails because `app.factor_research.parameters` does not exist.

- [x] **Step 3: Implement strict Pydantic models and validators**

```python
class ParameterFamily(FactorResearchModel):
    family_id: str
    evaluator: str
    parameter_grid: list[int]
    current_parameter: int
    member_factor_ids: list[str]
    status: Literal["tunable", "blocked"] = "tunable"
```

- [x] **Step 4: Run tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py -q`

Expected: PASS.

### Task 2: 223 因子分类报告与 CLI

**Files:**
- Create: `scripts/build_factor_parameter_registry.py`
- Modify: `backend/app/factor_research/parameters.py`
- Test: `backend/tests/factor_research/test_parameters.py`

**Interfaces:**
- Consumes: latest catalog snapshot and `data/factor_research/parameters/families.json`.
- Produces:
  - `classify_factor_parameters(...)`
  - `build_classification_payload(catalog, registry, generated_at) -> dict`
  - JSON/Markdown classification report and ledger record.

- [x] **Step 1: Write failing classification tests**

```python
def test_classification_marks_family_members_grouped_and_unregistered_windows_unclassified() -> None:
    rows = classify_factor_parameters(factors, registry)
    assert by_id["idio_vol_20"].status == "grouped"
    assert by_id["momentum_999d"].status == "unclassified"
    assert by_id["roe_latest"].status == "fixed"
```

- [x] **Step 2: Run test to verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py -q`

Expected: FAIL because classification function is missing.

- [x] **Step 3: Implement deterministic classification**

Rules:

1. Explicit family member → `grouped`.
2. Explicit blocked factor → `blocked`.
3. Numeric window pattern not registered → `unclassified`.
4. Everything else → `fixed`.

- [x] **Step 4: Add P1 family seed manifest**

Create `data/factor_research/parameters/families.json` with the eight P1 families.

- [x] **Step 5: Add CLI and run report generation**

Run: `cd backend && uv run --frozen python ../scripts/build_factor_parameter_registry.py`

Expected: JSON and Markdown reports under `data/factor_research/parameters/reports/`, ledger record appended.

- [x] **Step 6: Run tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py -q`

Expected: PASS.

### Task 3: 注册表驱动的 8 族参数搜索

**Files:**
- Modify: `scripts/research_alt_factor_parameters.py`
- Test: `backend/tests/factor_research/test_parameter_scan.py`

**Interfaces:**
- Consumes: `ParameterFamilyRegistry`, evaluator registry, price/market panel.
- Produces:
  - `load_p1_families(data_dir) -> list[ParameterFamily]`
  - `select_robust_candidates(trials) -> list[dict]`
  - parameter result JSON with 1000-run initial MC and 5000-run final MC.

- [x] **Step 1: Write failing tests for registry-driven family selection**

```python
def test_select_robust_candidates_requires_positive_train_test_and_bootstrap() -> None:
    selected = select_robust_candidates(trials)
    assert [item["parameter"] for item in selected] == [20]
```

- [x] **Step 2: Run test and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameter_scan.py -q`

Expected: FAIL because shared selection function does not exist.

- [x] **Step 3: Replace hardcoded family metadata with manifest lookup**

Keep compute functions in the script; move family metadata into the registry.

- [x] **Step 4: Add two-stage Monte Carlo**

Initial: 1000 runs, block 20.

Finalists: 5000 runs for blocks 10, 20, 40.

Recommendation requires primary final CI lower bound > 0 and positive train/test directional IC.

- [x] **Step 5: Run tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameter_scan.py -q`

Expected: PASS.

### Task 4: 运行 P1、发布证据和报告

**Files:**
- Modify: `scripts/research_alt_factor_parameters.py`
- Output: `data/factor_research/research/parameters/p1_parameter_mc_2026-11-11.json`
- Output: `data/factor_research/research/parameters/p1_parameter_mc_2026-11-11.md`

**Interfaces:**
- Consumes: P0 registry and P1 evaluator functions.
- Produces: ledger record `factor_parameter_mc_published` with output SHA.

- [x] **Step 1: Run P0 registry build**

Run: `cd backend && uv run --frozen python ../scripts/build_factor_parameter_registry.py --output data/factor_research/parameters/reports/classification_2026-11-11.json`

Expected: classification counts and report.

- [x] **Step 2: Run P1 parameter research**

Run: `cd backend && uv run --frozen python ../scripts/research_alt_factor_parameters.py --output ../data/factor_research/research/parameters/p1_parameter_mc_2026-11-11.json`

Expected: 8 families, 32 initial trials, finalist results, no active version.

- [x] **Step 3: Verify ledger and output hashes**

Run: `cd backend && uv run --frozen python -c '<ledger verification script>'`

Expected: hash chain and evidence SHA pass.

### Task 5: 回归验证与提交

**Files:**
- Modify: only P0/P1 files and evidence.
- Test: full backend suite.

- [x] **Step 1: Run focused tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_parameters.py tests/factor_research/test_parameter_scan.py -q`

- [x] **Step 2: Run full backend tests**

Run: `cd backend && uv run --frozen pytest tests -q`

- [x] **Step 3: Run Ruff and diff checks**

Run: `cd backend && uv run --frozen ruff check app/factor_research/parameters.py tests/factor_research/test_parameters.py tests/factor_research/test_parameter_scan.py ../scripts/build_factor_parameter_registry.py ../scripts/research_alt_factor_parameters.py`

Run: `git diff --check`

- [x] **Step 4: Commit and push only P0/P1 files**

```bash
git add backend/app/factor_research/parameters.py \
  backend/tests/factor_research/test_parameters.py \
  backend/tests/factor_research/test_parameter_scan.py \
  scripts/build_factor_parameter_registry.py \
  scripts/research_alt_factor_parameters.py \
  data/factor_research/parameters \
  data/factor_research/research/parameters \
  data/factor_research/ledger/head.json \
  data/factor_research/ledger/records.jsonl
git commit -m "feat: add factor parameter screening and monte carlo"
git push origin main
```

## Self-Review

- Spec coverage: P0 classification and P1 eight-family parameter MC are covered. P2-P6 remain in the design spec for subsequent plans.
- Placeholder scan: no TBD/TODO; all output paths and commands are explicit.
- Type consistency: `ParameterFamily` is consumed by both registry CLI and parameter research script; `select_robust_candidates` operates on trial dictionaries with `parameter`, `full`, `train`, `test`, `bootstrap_full`.
