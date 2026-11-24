# 全仓研究合同 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有新增研究纳入可预注册、可分类、可发布、可审计、不可覆盖的强制合同，并为 GBM 合成行情研究提供合法类型与共享发布边界。

**Architecture:** 用 Pydantic 合同模型集中定义预注册、结果和类型专用准入；用共享发布 helper 负责 runner/preregistration/result/hash/ledger 绑定；用独立 auditor 扫描新研究、校验 grandfather 精确哈希并输出 CI 可见审计。历史正式回测语义、`StrategyBacktestService`、数据契约和研究策略均不改。

**Tech Stack:** Python 3.11+/3.12、Pydantic v2、pytest、现有 `ResearchLedger`、SHA-256 sidecar、GitHub Actions、Ruff。

**Spec:** `docs/superpowers/specs/2026-11-24-research-contract-design.md`

## Global Constraints

- `active_version` 必须为 `null`；`orders_generated=false`；`paper_target_generated=false`；`production_enabled=false`。
- 新增研究类型封闭为 spec §4 的 8 种；新增类型必须同时修改规范、Pydantic 模型、auditor 与测试。
- grandfather 只对精确路径 + 精确 SHA-256 生效；文件发生变化即视为新研究。
- 新研究 runner 必须声明 `RESEARCH_TYPE` 和 `RESEARCH_PREREGISTRATION`。
- `project_formal` 必须使用正式 `run_with_evidence` 并由 `require_formal_evidence` 校验。
- 结果文件只能新增；禁止覆盖既有结果或历史冻结文件。
- 所有新增/修改 artifact 必须有同名 `.sha256`；机器绝对路径不得写入底稿。
- 不修改现有 `StrategyBacktestService`、`BacktestEngine` 或正式回测语义。
- 任何测试失败均不得声称研究合同已完成。

---

### Task 1: 冻结历史 runner grandfather 清单

**Files:**
- Create: `scripts/build_research_grandfather_manifest.py`
- Create: `data/factor_research/research/research_contract_grandfather_2026-11-24.json`
- Create: `data/factor_research/research/research_contract_grandfather_2026-11-24.json.sha256`
- Test: `backend/tests/test_research_contract.py`

**Interfaces:**
- Produces: `build_manifest(root: Path) -> dict[str, Any]`
- Produces: JSON rows `{path, sha256, disposition, reason, generated_at}`
- Consumes: existing `scripts/research_*.py` and `data/strategies/ai/.iterations/**/evidence/run_*.py`

- [ ] **Step 1: Write the failing test**

```python
def test_grandfather_manifest_matches_frozen_runner_hashes() -> None:
    import hashlib, json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / "data/factor_research/research/research_contract_grandfather_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == len(payload["rows"])
    assert payload["rows"]
    for row in payload["rows"]:
        runner = root / row["path"]
        assert runner.is_file(), row["path"]
        assert hashlib.sha256(runner.read_bytes()).hexdigest() == row["sha256"], row["path"]
        assert row["disposition"] == "legacy_grandfathered"
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py::test_grandfather_manifest_matches_frozen_runner_hashes -q`

Expected: FAIL with `FileNotFoundError` for the grandfather manifest or test module.

- [ ] **Step 3: Implement the manifest builder**

```python
def build_manifest(root: Path) -> dict[str, Any]:
    paths = sorted((root / "scripts").glob("research_*.py"))
    paths += sorted((root / "data/strategies/ai/.iterations").rglob("evidence/run_*.py"))
    rows = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        rows.append({
            "path": relative,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "disposition": "legacy_grandfathered",
            "reason": "Hashes frozen before mandatory research contract took effect.",
        })
    return {
        "schema_version": 1,
        "generated_at": "2026-11-24",
        "count": len(rows),
        "rows": rows,
    }
```

- [ ] **Step 4: Generate manifest and sidecar**

Run: `backend/.venv/bin/python scripts/build_research_grandfather_manifest.py`

Expected: manifest count equals the discovered runner count; sidecar matches.

- [ ] **Step 5: Run the test and commit**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py::test_grandfather_manifest_matches_frozen_runner_hashes -q`

Expected: PASS.

```bash
git add scripts/build_research_grandfather_manifest.py data/factor_research/research/research_contract_grandfather_2026-11-24.json data/factor_research/research/research_contract_grandfather_2026-11-24.json.sha256 backend/tests/test_research_contract.py
git commit -s -m "research: freeze pre-contract runner inventory"
```

### Task 2: 实现严格合同模型

**Files:**
- Create: `backend/app/factor_research/research_contract.py`
- Modify: `backend/tests/test_research_contract.py`

**Interfaces:**
- Produces: `ResearchType = Literal[...]`
- Produces: `ConclusionLevel = Literal[...]`
- Produces: `ResearchOutputPolicy`
- Produces: `ResearchExecutionContext`
- Produces: `SyntheticMarketSpec`
- Produces: `ResearchPreregistration`
- Produces: `ResearchResultEnvelope`
- Produces: `validate_result_for_preregistration(preregistration, result) -> list[str]`

- [ ] **Step 1: Write failing schema tests**

```python
def test_research_preregistration_rejects_execution_outputs() -> None:
    from app.factor_research.research_contract import ResearchPreregistration

    with pytest.raises(ValueError):
        ResearchPreregistration.model_validate({
            "schema_version": 1,
            "research_id": "R-X",
            "research_type": "non_return_research",
            "question": "q",
            "hypothesis": "h",
            "study_scope": "research_only",
            "runner": "scripts/research_x.py",
            "runner_sha256": "a" * 64,
            "data_source": "project",
            "output_policy": {
                "active_version": 1,
                "orders_generated": False,
                "paper_target_generated": False,
                "production_enabled": False,
            },
        })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py::test_research_preregistration_rejects_execution_outputs -q`

Expected: FAIL importing `ResearchPreregistration`.

- [ ] **Step 3: Implement Pydantic models with fail-closed validators**

Implement frozen, `extra="forbid"` models. At minimum validate:

```python
ResearchType = Literal[
    "project_formal", "statistical_screen", "feasibility_probe",
    "attribution", "derived_analysis_only", "synthetic_market_stress",
    "diagnostic_simulation", "non_return_research",
]
ConclusionLevel = Literal[
    "admitted_for_research", "watch_only", "rejected",
    "inconclusive", "data_gap", "diagnostic_only",
]
```

`ResearchOutputPolicy` requires `active_version is None` and all execution fields false.
`SyntheticMarketSpec` requires `model`, `parameters`, `seed`, `path_count >= 2`,
and a non-empty benchmark declaration. Reject `synthetic_*` without this object and
reject `project_formal` when execution is not `open_t+1`, leverage not `none`, or
`max_exposure_pct > 1.0`.

- [ ] **Step 4: Write cross-document tests**

```python
def test_result_requires_matching_preregistration_hash() -> None:
    from app.factor_research.research_contract import (
        ResearchPreregistration, ResearchResultEnvelope,
        validate_result_for_preregistration,
    )
    prereg = ResearchPreregistration.model_validate(base_prereg())
    result = ResearchResultEnvelope.model_validate(base_result())
    errors = validate_result_for_preregistration(prereg, result)
    assert "preregistration_sha256 mismatch" in errors
```

- [ ] **Step 5: Run contract tests and commit**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py -q`

Expected: PASS for schema and cross-document tests.

```bash
git add backend/app/factor_research/research_contract.py backend/tests/test_research_contract.py
git commit -s -m "research: add strict research contract models"
```

### Task 3: 实现不可覆盖发布与 ledger 绑定

**Files:**
- Modify: `backend/app/factor_research/research_contract.py`
- Modify: `backend/tests/test_research_contract.py`

**Interfaces:**
- Consumes: `ResearchPreregistration`, `ResearchResultEnvelope`,
  `app.factor_research.automation.bind_research_evidence`
- Produces: `publish_research_artifacts(data_dir, preregistration_path, result, markdown, *, operation) -> PublishedResearch`

- [ ] **Step 1: Write failing tests for overwrite refusal and deterministic publish**

```python
def test_publish_refuses_existing_result(tmp_path: Path) -> None:
    target = tmp_path / "factor_research/research/r-test-result.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}
", encoding="utf-8")
    with pytest.raises(FileExistsError):
        publish_research_artifacts(
            tmp_path,
            preregistration_path=write_prereg(tmp_path),
            result=base_result(),
            markdown="# result\n",
            operation="research_contract_test",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py::test_publish_refuses_existing_result -q`

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement atomic publish**

Requirements:

- validate prereg/result and all type-specific fields before writing;
- reject any existing JSON, Markdown, or sidecar at target paths;
- compute sidecars from exact final bytes;
- write via temp file + replace only for absent target;
- append/reuse ledger binding with the exact evidence hashes;
- return result path, hashes, and ledger record.

- [ ] **Step 4: Test idempotence and hash drift**

```python
def test_republish_same_bytes_is_idempotent(tmp_path: Path) -> None:
    first = publish_research_artifacts(...)
    second = publish_research_artifacts(...)
    assert first.result_sha256 == second.result_sha256
    assert first.ledger_record_id == second.ledger_record_id
```

Also assert that changing Markdown or result bytes raises `ValueError` rather than
rewriting historical output.

- [ ] **Step 5: Run tests and commit**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py -q`

Expected: PASS.

```bash
git add backend/app/factor_research/research_contract.py backend/tests/test_research_contract.py
git commit -s -m "research: publish immutable contract evidence"
```

### Task 4: 实现全仓合同审计器

**Files:**
- Create: `scripts/audit_research_contract.py`
- Create: `data/factor_research/research/research_contract_audit_2026-11-24.json`
- Create: `data/factor_research/research/research_contract_audit_2026-11-24.json.sha256`
- Modify: `backend/tests/test_research_contract.py`

**Interfaces:**
- Consumes: grandfather manifest and `app.factor_research.research_contract`
- Produces: `audit_repository(root: Path) -> dict[str, Any]`
- Produces: JSON with `gates`, `counts`, `runners`, `errors`

- [ ] **Step 1: Write failing tests for grandfather and changed-runner behavior**

```python
def test_changed_grandfathered_runner_requires_contract(tmp_path: Path) -> None:
    # copy a frozen runner + manifest into tmp_path
    # mutate runner bytes
    # assert audit_repository reports contract_required
    ...
```

- [ ] **Step 2: Implement auditor discovery and exact hash exemption**

Discovery paths are exactly:

```python
sorted((root / "scripts").glob("research_*.py"))
sorted((root / "data/strategies/ai/.iterations").rglob("evidence/run_*.py"))
```

Known grandfather path + matching SHA is accepted; changed bytes is an error.
New runner requires both `RESEARCH_TYPE` and `RESEARCH_PREREGISTRATION`.
Declared prereg must exist, have a valid sidecar, and contain matching runner hash.
Every contract result JSON must contain one valid result envelope and match its
preregistration hash. Contract results must not be overwritten; `generated_at`
must be on/after `created_at`. Any output policy violation fails closed.

- [ ] **Step 3: Run auditor tests to verify failure is gone**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py -q`

Expected: PASS.

- [ ] **Step 4: Generate audit and sidecar**

Run: `python3 scripts/audit_research_contract.py`

Expected: current legacy runners accepted only through grandfather; all explicit
contract errors zero.

- [ ] **Step 5: Commit**

```bash
git add scripts/audit_research_contract.py data/factor_research/research/research_contract_audit_2026-11-24.json data/factor_research/research/research_contract_audit_2026-11-24.json.sha256 backend/tests/test_research_contract.py
git commit -s -m "research: enforce repository contract audit"
```

### Task 5: 接入 CI 与文档入口

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `CONTRIBUTING.md`
- Modify: `docs/README.md`
- Test: `backend/tests/test_research_contract.py`

**Interfaces:**
- Consumes: `scripts/audit_research_contract.py`
- Produces: CI step `研究合同审计`

- [ ] **Step 1: Write failing CI contract test**

```python
def test_ci_runs_research_contract_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/audit_research_contract.py" in workflow
```

- [ ] **Step 2: Add CI step after dependency install and before full tests**

```yaml
      - name: 研究合同审计
        run: python scripts/audit_research_contract.py
```

- [ ] **Step 3: Add concise documentation links and mandatory workflow**

Document that all new research follows the spec/plan/preregistration/result/ledger
sequence and that grandfather is limited to exact hashes.

- [ ] **Step 4: Run focused tests and commit**

Run: `cd backend && uv run --frozen pytest tests/test_research_contract.py -q`

Expected: PASS.

```bash
git add .github/workflows/ci.yml CONTRIBUTING.md docs/README.md backend/tests/test_research_contract.py
git commit -s -m "docs: require research contract in CI"
```

### Task 6: 合同验收与 GBM 入口

**Files:**
- Create: `docs/superpowers/specs/2026-11-24-gbm-market-stress-design.md`
- Create: `docs/superpowers/plans/2026-11-24-gbm-market-stress.md`
- Modify: `data/factor_research/research/research_standard_rules_2026-11-24.json`
- Test: `backend/tests/test_research_contract.py`

**Interfaces:**
- Consumes: all prior tasks
- Produces: contract-approved design/plan for `synthetic_market_stress`

- [ ] **Step 1: Write failing test that standard rules point to the contract**

Add an assertion that the standard-rules JSON declares
`contract_spec=docs/superpowers/specs/2026-11-24-research-contract-design.md`
and includes `synthetic_market_stress` in the closed type list.

- [ ] **Step 2: Update standard rules**

Add the type, grandfather rule, and contract path without rewriting historical results.

- [ ] **Step 3: Write GBM spec and plan**

Spec must define pure GBM path generation, seeds, path count, drift/vol scenarios,
same-path equal-weight benchmark, project execution adapter, and non-promotion rules.
Plan must use the contract's preregistration/result/ledger helpers.

- [ ] **Step 4: Run all contract and conformance tests**

Run:

```bash
cd backend && uv run --frozen pytest tests/test_research_contract.py tests/test_research_conformance_contract.py tests/test_research_hygiene.py -q
python3 scripts/audit_research_contract.py
python3 scripts/audit_research_conformance.py
python3 scripts/audit_research_research_assets.py
```

Expected: all PASS / exit 0.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-11-24-gbm-market-stress-design.md docs/superpowers/plans/2026-11-24-gbm-market-stress.md data/factor_research/research/research_standard_rules_2026-11-24.json
git commit -s -m "research: approve GBM stress under contract"
```

## Self-Review

**Spec coverage:** §4 types → Task 2; §5 preregistration → Tasks 2–3; §6 type-specific
rules → Task 2; §7 runner declarations → Task 4; §8 result envelope → Tasks 2–3;
§9 shared code/auditor → Tasks 3–4; §10 grandfather → Task 1; §11 CI/tests → Tasks 4–5;
§13 GBM path → Task 6.

**Placeholder scan:** No TBD/TODO placeholders. Test snippets that use `...` are only
in failure-first examples where the exact model fields are defined in Task 2; implementation
steps must replace them with concrete fixtures during Task 2.

**Type consistency:** `ResearchPreregistration`, `ResearchResultEnvelope`,
`validate_result_for_preregistration`, `publish_research_artifacts`, and
`audit_repository` are consistently named across tasks.
