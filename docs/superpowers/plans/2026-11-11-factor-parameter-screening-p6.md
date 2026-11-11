# 因子参数筛选 P6 自动闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 catalog 变化后自动完成新增因子发现、参数分类、审计/PIT 准入、待跑队列生成、ResearchLedger 绑定和不可变历史归档，并让未通过准入门禁的因子无法进入新的组合版本。

**Architecture:** P6 新增一个纯函数准入层和一个单一编排脚本。纯函数只消费 catalog、参数注册表、最新数据审计和 P1-P5 已发布证据，输出确定性的 readiness/task payload；脚本把 payload 写入按 research ID 命名的不可变运行目录，更新 latest 指针并自动追加或复用 ResearchLedger 记录。P5 建 watch 前调用同一准入守卫，避免另一套规则。

**Tech Stack:** Python 3.11、Pydantic、Polars、ResearchLedger、FactorSetStore、pytest、JSON/Markdown evidence、GitHub Actions。

**Spec:** `docs/superpowers/specs/2026-11-11-factor-parameter-screening-monte-carlo-design.md`

> **Execution note (2026-11-11):** P6 已完成实现与真实运行。Catalog
> `sha256:ea0642e2c74326752bc109dcc61d72abe7f65776e107d42654f6b865e30dd79a`
> 下共 223 个因子，分类 `fixed=121 / grouped=82 / blocked=20 / unclassified=0`。
> 85 个参数族均有研究结论；54 个扩展族因审计配置指纹过期被自动阻断，未静默进入组合。
> Research ID 为 `sha256:06427fb38dfd609625af2fc91b0800b9ce9c3f01b50039f3b4533979c00f18eb`，
> JSON/Markdown SHA 分别为
> `sha256:c67d0c2db07d961d48fd00c2959a1d9fefd043742bd55507f32b81e7c4be7dc0` 和
> `sha256:800aa4fda1c403a2cc8fe8bf59e92c063904332a153e4e970b678ea8fd874da0`。
> 重复运行、以及模拟缺少被 gitignore 的扩展配置时，research ID、输出文件和 ledger 均保持不变。
> 最终通过提交为 `3c6f32c4`，CI 与 Docker 均成功。

> **Post-acceptance note (2026-11-11 P7):**
> 扩展审计刷新后，P6 首次重跑发现两个自动化边界问题：最新审计按目录顺序而非
> `generated_at` 选择；catalog 变化后的下一次重跑会把 `catalog_delta` 从
> incremental 重置为 baseline，造成 Research ID 漂移。两处均已补回归测试并修复。
> 财务 P7 使 15 个财务因子通过逐日 PIT 复算，catalog 重建为
> `sha256:6cb4e69da0a5a3097459e32ddd662979c4602f7327c3b737c4130de147b9b3f6`。
> P6 当前 Research ID 为
> `sha256:5cdd753a0e3037f82fba50f3e77a056a8c6353b21cdff6feb1daf9d8fc420ce9`，
> `164/164` 个可研究因子通过准入，`blocked_task_count=0`，`active_version=null`。
> P6 绑定的是新 catalog 上重跑的 P5 Research ID
> `sha256:641f0df5849264ea133ea0a8ccb58b057816afd47d916014f0d5baabb93cb6c7`；
> P5 仍没有组合通过，未创建新 watch 版本。

## Global Constraints

- 不自动激活策略，不生成纸面目标或订单；`active_version` 继续为 null。
- 用户不上杠杆；准入层不得引入杠杆要求。
- 2x 成本为正常基准，3x 为硬压力；5x 只能作为 warning，不能成为拒绝条件。
- 当前 HS300 名单回填历史存在幸存者偏差，报告必须保留。
- 未分类、未来数据未知、PIT unknown、审计缺失、审计失败或审计已过期的因子必须 fail-closed。
- 财务因子进入组合前必须 `pit_status=verified`；只允许在参数研究队列中使用 `declared`。
- 相同 catalog、注册表、审计、研究证据和 ledger 输入必须生成同一 research ID。
- 重复运行不得覆盖历史运行，不得重复追加 ledger 记录。
- 失败任务必须进入 `negative_knowledge`，不能静默跳过。

## File Structure

- `backend/app/factor_research/automation.py`：纯准入、研究覆盖、待跑队列和 ledger 绑定原语。
- `backend/tests/factor_research/test_automation.py`：准入、分类、审计、PIT、待跑队列测试。
- `backend/tests/factor_research/test_automation_ledger.py`：ResearchLedger 幂等绑定和冲突测试。
- `scripts/research_factor_automation_p6.py`：读取真实输入、生成 payload、写入不可变运行、更新 latest。
- `backend/tests/factor_research/test_automation_p6_scan.py`：真实 P6 产物、幂等和组合守卫测试。
- `scripts/research_factor_increment_p5.py`：建 watch 前调用统一组合准入守卫。
- `data/factor_research/automation/runs/*.json|*.md`：按 research ID 不可变保存。
- `data/factor_research/automation/latest.json`：指向最近一次运行，不包含全量 payload。

---

### Task 1: 因子 readiness 与组合准入纯函数

**Files:**
- Create: `backend/app/factor_research/automation.py`
- Create: `backend/tests/factor_research/test_automation.py`

**Interfaces:**
- Produces: `evaluate_factor_readiness(catalog: CatalogSnapshot, classifications: Sequence[FactorParameterClassification], audit: Mapping[str, Any] | None, *, current_audit_config_sha256: str | None) -> dict[str, dict[str, Any]]`
- Produces: `assert_portfolio_candidates_admitted(data_dir: Path, factor_ids: Sequence[str]) -> dict[str, Any]`
- Each readiness row contains `scan_ready: bool`, `portfolio_ready: bool`, `reasons: list[str]`, `audit_status: str`, `classification_status: str`, `pit_status: str`, `future_data_status: str`.

- [x] **Step 1: Write failing readiness tests**

```python
def test_unclassified_factor_is_blocked_for_scan_and_portfolio():
    row = evaluate_factor_readiness(...)[\"factor_x\"]
    assert row[\"scan_ready\"] is False
    assert \"unclassified\" in row[\"reasons\"]


def test_financial_declared_pit_can_research_but_cannot_enter_portfolio():
    row = evaluate_factor_readiness(...)[\"financial_x\"]
    assert row[\"scan_ready\"] is True
    assert row[\"portfolio_ready\"] is False
    assert \"financial_pit_not_verified\" in row[\"reasons\"]
```

- [x] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation.py -q -k 'unclassified or declared_pit'`
Expected: FAIL because `evaluate_factor_readiness` does not exist.

- [x] **Step 3: Implement readiness**

Implement the following precedence:

1. classification `unclassified` blocks both gates;
2. `future_data_status == "blocked"` blocks both gates;
3. `future_data_status != "clear"` blocks both gates;
4. `pit_status == "unknown"` blocks both gates;
5. extension without a current audit row is `audit_missing`;
6. extension audit `integrity != "pass"` is `audit_failed`;
7. extension audit config hash different from the current ext config hash is `audit_stale`;
8. financial factor with PIT other than `verified` keeps scan eligibility but blocks portfolio;
9. custom/composite source blocks automatic scan unless explicitly admitted by a future audited contract.

- [x] **Step 4: Add portfolio guard load/verify test**

The guard reads `data/factor_research/automation/latest.json`, verifies the referenced run exists and its `research_id` matches its canonical payload, then checks `admission.portfolio_ready` for every requested factor. Missing manifests and missing factor rows fail closed.

- [x] **Step 5: Run readiness tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation.py -q`
Expected: PASS.

---

### Task 2: 研究覆盖、新增候选与待跑队列

**Files:**
- Modify: `backend/app/factor_research/automation.py`
- Modify: `backend/tests/factor_research/test_automation.py`

**Interfaces:**
- Produces: `build_research_coverage(window_payloads: Mapping[str, Mapping[str, Any]], threshold_payload: Mapping[str, Any], incremental_payload: Mapping[str, Any], window_families: Sequence[ParameterFamily], threshold_families: Sequence[ThresholdFamily]) -> dict[str, dict[str, Any]]`
- Produces: `build_automation_payload(*, catalog, window_registry, threshold_registry, window_payloads, threshold_payload, incremental_payload, ledger_bindings, previous_payload, generated_at) -> dict[str, Any]`

- [x] **Step 1: Write failing coverage tests**

```python
def test_unresearched_family_generates_parameter_scan_task():
    payload = build_automation_payload(...)
    assert payload[\"pending_tasks\"][0][\"kind\"] == \"parameter_scan\"


def test_p5_reference_resolves_recommended_variant_without_pending_registration():
    payload = build_automation_payload(...)
    assert payload[\"pending_tasks\"] == []
```

- [x] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation.py -q -k 'coverage or pending_registration'`
Expected: FAIL because coverage helpers do not exist.

- [x] **Step 3: Implement coverage mapping**

Build per-family coverage from P1-P4 family payloads. Resolve registration/incremental gaps only when the P5 payload references the exact current upstream research ID and covers the family/source factor. A recommended family without P5 coverage emits `parameter_scan` or `factor_registration` tasks; never silently assume the recommendation was tested.

- [x] **Step 4: Implement catalog delta and classification discovery**

Compare current catalog factor IDs with the previous automation run. On first run use `catalog_delta.mode="baseline"`; later runs emit `new_factor_ids` and `removed_factor_ids`. Every current factor must appear in classification; every numeric-window factor outside a registered family becomes `unclassified` and a blocked task.

- [x] **Step 5: Implement deterministic payload**

Use `sha256_json` over catalog ID/fingerprint, parameter and threshold registries, research IDs, audit ID/findings hash, ledger record IDs, readiness rows and tasks. Exclude `generated_at` from the research ID.

- [x] **Step 6: Run tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation.py -q`
Expected: PASS.

---

### Task 3: ResearchLedger 自动绑定与不可变运行存储

**Files:**
- Modify: `backend/app/factor_research/automation.py`
- Create: `backend/tests/factor_research/test_automation_ledger.py`

**Interfaces:**
- Produces: `bind_research_evidence(data_dir: Path, *, operation: str, evidence_paths: Sequence[Path], research_id: str, payload: Mapping[str, Any]) -> tuple[LedgerRecord, bool]`
- Produces: `audit_research_bindings(data_dir: Path, bindings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]`

- [x] **Step 1: Write failing ledger idempotency tests**

```python
def test_bind_research_evidence_is_idempotent(tmp_path):
    first, created = bind_research_evidence(...)
    second, created_again = bind_research_evidence(...)
    assert first == second
    assert created is True and created_again is False
    assert len(ResearchLedger(tmp_path).records()) == 1


def test_changed_evidence_hash_fails_closed(tmp_path):
    with pytest.raises(ValueError, match=\"evidence hash mismatch\"):
        bind_research_evidence(...)
```

- [x] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation_ledger.py -q`
Expected: FAIL because binding helpers do not exist.

- [x] **Step 3: Implement bind/audit**

Normalize evidence paths relative to `data_dir`, hash every file, store `research_id`, `output_sha256`, `markdown_sha256`, and `evidence_sha256` in `input_fingerprints`, and return the existing matching record when all hashes match. Reject duplicate operation/research-ID records, path mismatch, hash mismatch, and multiple latest outputs.

- [x] **Step 4: Run ledger tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation_ledger.py -q`
Expected: PASS.

---

### Task 4: P6 编排脚本与真实产物

**Files:**
- Create: `scripts/research_factor_automation_p6.py`
- Create: `backend/tests/factor_research/test_automation_p6_scan.py`
- Create: `data/factor_research/automation/runs/<research_token>.json`
- Create: `data/factor_research/automation/runs/<research_token>.md`
- Create: `data/factor_research/automation/latest.json`

- [x] **Step 1: Write failing scan tests**

```python
def test_published_p6_result_is_complete_and_watch_only():
    payload = json.loads(...)
    assert payload[\"summary\"][\"active_version\"] is None
    assert payload[\"classification\"][\"unclassified_count\"] == 0
    assert payload[\"ledger\"][\"operation\"] == \"factor_research_automation_published\"


def test_p6_rerun_does_not_change_ledger_or_outputs():
    before = sha256_files([...])
    subprocess.run([...], check=True)
    assert sha256_files([...]) == before
```

- [x] **Step 2: Run tests and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_automation_p6_scan.py -q`
Expected: FAIL because the script and outputs do not exist.

- [x] **Step 3: Implement script**

Load latest catalog, both parameter registries, latest audit, P1-P4 results and P5 incremental payload. Validate P1-P5 ledger bindings. Build payload. Write `runs/<research_token>.json` and `.md` only when absent; if present, verify canonical research ID and reuse. Write latest pointer idempotently. Bind the P6 payload to ResearchLedger as `factor_research_automation_published`.

- [x] **Step 4: Run script twice and compare hashes**

Run: `cd backend && uv run --frozen python ../scripts/research_factor_automation_p6.py`
Run again and compare JSON/Markdown/latest/ledger hashes.
Expected: identical hashes and no new ledger record.

---

### Task 5: P5 组合准入守卫接线

**Files:**
- Modify: `scripts/research_factor_increment_p5.py`
- Modify: `backend/tests/factor_research/test_increment_p5_scan.py`

**Interfaces:**
- Consumes: `assert_portfolio_candidates_admitted(data_dir: Path, factor_ids: Sequence[str]) -> dict[str, Any]`

- [x] **Step 1: Write failing P5 guard test**

Create a temporary latest manifest with a blocked candidate and assert `_ensure_watch_version` raises before calling `FactorSetStore.create_version`.

- [x] **Step 2: Run test and verify failure**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_increment_p5_scan.py -q -k portfolio_admission`
Expected: FAIL because P5 does not call the guard.

- [x] **Step 3: Wire guard**

Call the guard with new factor IDs after the final factor set is selected and before `_ensure_watch_version`. Existing idempotent no-new-watch runs must still succeed without creating a version.

- [x] **Step 4: Run P5 and P6 tests**

Run: `cd backend && uv run --frozen pytest tests/factor_research/test_increment_p5_scan.py tests/factor_research/test_automation_p6_scan.py -q`
Expected: PASS.

---

### Task 6: 全链路验收

**Files:**
- Modify: `docs/superpowers/plans/2026-11-11-factor-parameter-screening-p6.md`

- [x] **Step 1: Run focused tests**

```bash
cd backend
uv run --frozen pytest \
  tests/factor_research/test_automation.py \
  tests/factor_research/test_automation_ledger.py \
  tests/factor_research/test_automation_p6_scan.py \
  tests/factor_research/test_increment_p5_scan.py \
  tests/factor_research/test_catalog.py \
  tests/factor_research/test_sets.py -q
```

- [x] **Step 2: Run full validation**

```bash
cd backend && uv run --frozen pytest tests -q
cd backend && uv run --frozen ruff check app/factor_research/automation.py tests/factor_research/test_automation.py tests/factor_research/test_automation_ledger.py tests/factor_research/test_automation_p6_scan.py ../scripts/research_factor_automation_p6.py ../scripts/research_factor_increment_p5.py
cd frontend && pnpm build
git diff --check
```

- [x] **Step 3: Commit exactly P6 files**

Do not stage `.gitignore`, R359-R371, weekly reports, unrelated custom factors/signals, or AI iteration ledger files.

- [x] **Step 4: Push and verify GitHub CI + Docker**
  CI `https://github.com/WW-shan/hs300/actions/runs/43040485256`；
  Docker `https://github.com/WW-shan/hs300/actions/runs/43040485273`。

Record both run URLs in this plan and require success before P6 completion.

## Self-Review

- Catalog delta, classification, audit and PIT gates are represented as explicit readiness rows.
- Every blocked task writes reasons into `negative_knowledge`.
- P5 uses the same generated admission manifest; no duplicate ad-hoc gate exists.
- Run paths are content-addressed by research ID; reruns reuse bytes and ledger records.
- Failed ledger verification, missing latest pointers and stale audits fail closed.
