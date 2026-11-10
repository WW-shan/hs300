# 因子集与持续研究系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有因子注册表、回测和评分链路之上，实现可版本化的因子母集、研究池、不可变因子集、追加式台账和分层审计。

**Architecture:** 新增 `backend/app/factor_research/` 研究层，复用现有 `FactorSpec`、DSL、扩展字段和 `FactorBacktestService`。母集和研究定义写入 `data/factor_research/`，所有变更通过不可变 JSON 版本和 `records.jsonl` 台账完成；API 只负责校验和编排。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、Polars、pytest、uv、现有 `app.factors`/`app.backtest` 接口。

**Spec:** `docs/superpowers/specs/2026-11-10-factor-set-research-system-design.md`

## Global Constraints

- 不修改因子公式、指标管道、评分引擎或回测引擎的业务逻辑。
- 不改变已有 R359-R371 未提交文件；新增代码不得覆盖它们。
- `stable` 只表示定义稳定，不能映射为有效或 active。
- 物理列、虚拟字段、历史定义和悬空引用必须分开解析。
- 财务 PIT 默认只算声明；没有公告日证据不能进入 active。
- 无杠杆；2× 是基准成本，3× 是压力，5× 只能产生 warning，不能单独硬拒绝。
- `data/factor_research/` 的永久研究资产要进入 Git；Parquet、NAV、临时扫描和 `cache/` 保持忽略。
- 所有生产代码改动先写失败测试，再实现；不自动提交、不自动推送。
- 当前工作区已有大量未提交研究文件，任何 `git add` 只能包含本任务新增/修改的文件，且提交动作必须由用户另行确认。

---

## File Structure

### 新增研究层

- `backend/app/factor_research/__init__.py`：公开服务入口。
- `backend/app/factor_research/fingerprints.py`：规范化 JSON、SHA-256、内容 hash。
- `backend/app/factor_research/models.py`：Pydantic schema、枚举和错误类型。
- `backend/app/factor_research/locking.py`：跨平台短文件锁和原子 JSON 写入。
- `backend/app/factor_research/references.py`：六类引用解析和历史别名加载。
- `backend/app/factor_research/strategy_audit.py`：策略/复合/信号引用扫描。
- `backend/app/factor_research/catalog.py`：母集快照构建、保存和 latest 指针。
- `backend/app/factor_research/ledger.py`：追加式 JSONL 台账、哈希链和 head 派生。
- `backend/app/factor_research/pools.py`：研究池不可变 revision。
- `backend/app/factor_research/sets.py`：不可变因子集版本、head 和过期判定。
- `backend/app/factor_research/policies.py`：policy 读取和校验。
- `backend/app/factor_research/audit.py`：覆盖率、横截面分类、PIT 状态和审计落盘。
- `backend/app/factor_research/scoring_adapter.py`：因子集到现有 scoring dict 的显式转换。
- `backend/app/factor_research/bootstrap.py`：初始池和 `core_v1` 种子。
- `backend/app/api/factor_research.py`：只读和少量写接口。
- `scripts/sync_factor_research_catalog.py`：母集同步 CLI。
- `scripts/bootstrap_factor_research.py`：初始池/集合初始化 CLI。
- `scripts/audit_factor_research.py`：离线数据审计 CLI。

### 初始研究资产

- `data/factor_research/catalog/field_taxonomy.json`
- `data/factor_research/catalog/historical_aliases.json`
- `data/factor_research/policies/default_v1.json`
- `data/factor_research/policies/core_v1.json`
- `data/factor_research/pools/core_v1/revisions/r0001.json`
- `data/factor_research/sets/core_v1/versions/v0001.json`
- `data/factor_research/ledger/schema.json`

### 测试

- `backend/tests/factor_research/test_fingerprints.py`
- `backend/tests/factor_research/test_references.py`
- `backend/tests/factor_research/test_catalog.py`
- `backend/tests/factor_research/test_ledger.py`
- `backend/tests/factor_research/test_pools.py`
- `backend/tests/factor_research/test_sets.py`
- `backend/tests/factor_research/test_audit.py`
- `backend/tests/factor_research/test_api.py`
- `backend/tests/factor_research/test_bootstrap.py`

---

### Task 1: 指纹、模型和原子写

**Files:**
- Create: `backend/app/factor_research/__init__.py`
- Create: `backend/app/factor_research/fingerprints.py`
- Create: `backend/app/factor_research/models.py`
- Create: `backend/app/factor_research/locking.py`
- Test: `backend/tests/factor_research/test_fingerprints.py`

**Interfaces:**
- Produces:
  - `canonical_json(value: object) -> str`
  - `sha256_text(value: str) -> str`，返回 `sha256:<64-hex>`
  - `sha256_json(value: object) -> str`
  - `content_hash(payload: Mapping[str, object], *excluded: str) -> str`
  - `utc_now() -> str`
  - `FactorResearchModel`（frozen、extra=forbid 的 Pydantic 基类）
  - `atomic_write_json(path: Path, payload: object) -> None`
  - `FileLock(path: Path)` 上下文管理器

- [ ] **Step 1: 写失败测试**

```python
from app.factor_research.fingerprints import canonical_json, content_hash, sha256_json


def test_canonical_json_is_stable_for_key_order():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_content_hash_ignores_excluded_fields():
    payload = {"a": 1, "b": 2, "content_sha256": "old"}
    assert content_hash(payload, "content_sha256") == sha256_json({"a": 1, "b": 2})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_fingerprints.py -q
```

预期：`ModuleNotFoundError: app.factor_research`。

- [ ] **Step 3: 实现最小代码**

`fingerprints.py` 使用：

```python
def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
```

`models.py` 定义 `FactorResearchModel`、`ReferenceKind`、`Role`、`MembershipStatus`、
`SetState`、`DataClass`、`PitStatus`、`FutureDataStatus`、`EvidenceVerdict`。

`locking.py` 使用临时文件 + `flush` + `fsync` + `os.replace`；文件锁在 Windows 使用
`msvcrt`，其他平台使用 `fcntl`。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_fingerprints.py -q
```

预期：通过。

---

### Task 2: 六类引用解析和策略引用扫描

**Files:**
- Create: `backend/app/factor_research/references.py`
- Create: `backend/app/factor_research/strategy_audit.py`
- Test: `backend/tests/factor_research/test_references.py`

**Interfaces:**
- Consumes: `FactorSpec`、`all_factors`、`virtual_dependencies`、`BASE_COLUMNS`、`ENRICHED_COLUMNS`、`ext_string_fields`。
- Produces:
  - `ResolvedReference`
  - `ReferenceResolver.resolve(reference_id: str) -> ResolvedReference`
  - `load_historical_aliases(data_dir: Path) -> list[HistoricalAlias]`
  - `audit_strategy_references(strategies: Iterable[Mapping], resolver) -> StrategyReferenceAudit`

- [ ] **Step 1: 写失败测试**

```python
from app.factor_research.references import ReferenceResolver


def test_physical_columns_are_not_missing_factors():
    resolver = ReferenceResolver.from_current()
    assert resolver.resolve("amount").reference_kind == "physical_column"
    assert resolver.resolve("consecutive_limit_ups").reference_kind == "physical_column"


def test_unknown_reference_is_dangling():
    resolver = ReferenceResolver.from_current()
    assert resolver.resolve("not_a_real_factor").reference_kind == "dangling_reference"


def test_virtual_field_is_distinguished_from_factor():
    resolver = ReferenceResolver.from_current()
    resolved = resolver.resolve("turnover_z_60d")
    assert resolved.reference_kind in {"registered_factor", "virtual_scoring_field"}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_references.py -q
```

- [ ] **Step 3: 实现解析顺序**

严格按 `registered_factor → virtual_scoring_field → physical_column → historical_definition →
signal_field → dangling_reference`。禁止前缀匹配和模糊名称。

`strategy_audit.py` 对策略 payload 的 `scoring` key、复合 `members`、`overlay_filter`
字段引用和自定义信号字段递归提取；输出六类计数和 dangling 列表。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_references.py -q
```

---

### Task 3: 母集快照和来源 fingerprint

**Files:**
- Create: `backend/app/factor_research/catalog.py`
- Test: `backend/tests/factor_research/test_catalog.py`

**Interfaces:**
- Consumes: `ReferenceResolver`、`all_factors`、`ext_factor_specs`、`load_into_registry`。
- Produces:
  - `build_catalog(data_dir: Path, *, generated_at: str | None = None) -> CatalogSnapshot`
  - `save_catalog(data_dir: Path, snapshot: CatalogSnapshot) -> Path`
  - `load_latest_catalog(data_dir: Path) -> CatalogSnapshot | None`
  - `load_catalog(data_dir: Path, snapshot_id: str) -> CatalogSnapshot`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
from app.factor_research.catalog import build_catalog, save_catalog


def test_catalog_contains_registry_and_physical_columns(tmp_path: Path):
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    assert snapshot.factor_count >= 1
    assert snapshot.factors[0].factor_id
    assert any(column.column_id == "amount" for column in snapshot.physical_columns)
    assert snapshot.snapshot_id.startswith("sha256:")


def test_catalog_snapshot_roundtrip(tmp_path: Path):
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    path = save_catalog(tmp_path, snapshot)
    assert path.exists()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_catalog.py -q
```

- [ ] **Step 3: 实现**

`build_catalog` 对每个 `FactorSpec` 生成 `FactorRecord`，记录：

- id/version/kind/group/formula/dependencies/components；
- PIT、asset_types、scale_free、incremental_safe、definition_stability；
- source locator；
- field taxonomy 的 `data_class`，缺失为 `unknown`；
- definition fingerprint、reference kind。

同时生成 `physical_columns`、`virtual_scoring_fields`、`historical_definitions` 和
`load_errors`。内容相同复用 snapshot id；写入使用原子 JSON。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_catalog.py -q
```

---

### Task 4: 追加式台账

**Files:**
- Create: `backend/app/factor_research/ledger.py`
- Test: `backend/tests/factor_research/test_ledger.py`

**Interfaces:**
- Produces:
  - `ResearchLedger(data_dir: Path)`
  - `append(operation: str, *, payload: Mapping[str, object], actor: str = "research") -> LedgerRecord`
  - `records() -> list[LedgerRecord]`
  - `verify() -> None`
  - `head() -> LedgerHead`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
import pytest
from app.factor_research.ledger import ResearchLedger


def test_ledger_append_hash_chain(tmp_path: Path):
    ledger = ResearchLedger(tmp_path)
    first = ledger.append("catalog_published", payload={"snapshot_id": "sha256:a"})
    second = ledger.append("audit_completed", payload={"audit_id": "sha256:b"})
    assert second.ledger_prev_hash == first.record_hash
    ledger.verify()
    assert [item.sequence for item in ledger.records()] == [1, 2]


def test_ledger_rejects_corrupt_middle_line(tmp_path: Path):
    ledger = ResearchLedger(tmp_path)
    ledger.append("catalog_published", payload={"snapshot_id": "sha256:a"})
    path = tmp_path / "factor_research" / "ledger" / "records.jsonl"
    path.write_text(path.read_text() + "{broken\\n", encoding="utf-8")
    with pytest.raises(Exception):
        ledger.verify()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_ledger.py -q
```

- [ ] **Step 3: 实现**

`record_hash` 排除自身和 `record_id`，包含 `ledger_prev_hash`；`record_id` 由 record hash
前 24 位派生。写入使用 `factor_research/.write.lock`，追加后 fsync。读取时逐行验证
sequence、prev hash、record hash；损坏时抛出带行号的错误。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_ledger.py -q
```

---

### Task 5: 研究池版本和 head

**Files:**
- Create: `backend/app/factor_research/pools.py`
- Test: `backend/tests/factor_research/test_pools.py`

**Interfaces:**
- Consumes: `ResearchLedger`、`ReferenceResolver`。
- Produces:
  - `PoolStore(data_dir, resolver, ledger)`
  - `create_revision(pool_id, members, expected_revision, change_reason) -> PoolRevision`
  - `get(pool_id, revision: int | None = None) -> PoolRevision | None`
  - `list_heads() -> list[PoolHead]`

- [ ] **Step 1: 写失败测试**

```python
from app.factor_research.pools import PoolStore


def test_pool_revision_is_append_only(tmp_path):
    store = PoolStore(tmp_path)
    first = store.create_revision("core_v1", [], 0, "初始化")
    second = store.create_revision("core_v1", [], 1, "第二次")
    assert first.revision == 1
    assert second.parent_revision == 1
    assert store.get("core_v1", 1).change_reason == "初始化"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_pools.py -q
```

- [ ] **Step 3: 实现**

写入 `pools/<pool_id>/revisions/rNNNN.json`，禁止覆盖已有 revision；head 从台账最后一条
`pool_revision_created` 派生并原子写 `head.json`。成员必须唯一且引用可解析。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_pools.py -q
```

---

### Task 6: 不可变因子集版本、head 和过期判定

**Files:**
- Create: `backend/app/factor_research/sets.py`
- Test: `backend/tests/factor_research/test_sets.py`

**Interfaces:**
- Consumes: `ReferenceResolver`、`ResearchLedger`、`CatalogSnapshot`。
- Produces:
  - `FactorSetStore(data_dir, resolver, ledger)`
  - `create_version(set_id, members, parent_version, expected_head_sha256, change_reason) -> FactorSetVersion`
  - `set_head(set_id, state, version, expected_head_sha256, change_reason, audit_checker=None) -> SetHead`
  - `load_version(set_id, version) -> FactorSetVersion`
  - `head(set_id) -> SetHead`
  - `stale_reasons(version) -> list[str]`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from app.factor_research.sets import FactorSetStore


def test_set_version_cannot_be_overwritten(tmp_path):
    store = FactorSetStore(tmp_path)
    version = store.create_version("core_v1", [], None, None, "初始化")
    assert version.version == 1
    with pytest.raises(Exception):
        store.create_version("core_v1", [], None, None, "重复")


def test_active_requires_audit_checker(tmp_path):
    store = FactorSetStore(tmp_path)
    store.create_version("core_v1", [], None, None, "初始化")
    with pytest.raises(Exception):
        store.set_head("core_v1", "active", 1, None, "直接激活")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_sets.py -q
```

- [ ] **Step 3: 实现校验**

- alpha 只能是数值注册因子；
- 市场级因子不能进入个股 alpha；
- weight 非负，watch alpha 权重和为 1；
- active alpha 权重和为 1，watch alpha 不参与 scoring；
- PIT 财务成员未验证时不能 active；
- 旧版本已存在且 hash 不同则拒绝覆盖；
- active head 必须有 audit_checker，且返回 `integrity=pass`、`incremental=positive`、
  `parameter_status=pass`、`investability=paper_ready`；
- 版本文件只保存 preflight，不保存可变审计 verdict。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_sets.py -q
```

---

### Task 7: 审计、policy 和评分适配

**Files:**
- Create: `backend/app/factor_research/policies.py`
- Create: `backend/app/factor_research/audit.py`
- Create: `backend/app/factor_research/scoring_adapter.py`
- Test: `backend/tests/factor_research/test_audit.py`

**Interfaces:**
- Produces:
  - `load_policy(data_dir: Path, policy_id: str = "default_v1") -> ResearchPolicy`
  - `AuditService(data_dir, policy).audit_panel(panel: pl.DataFrame, factor_ids: list[str]) -> AuditResult`
  - `audit_result.audit_id`, `coverage`, `classification`, `findings`
  - `factor_set_to_scoring(version, *, research_only: bool = False) -> ScoringView`
- 审计输出写入 `audits/<audit_id>/manifest.json`、`metrics.json`、`findings.json`、`report.md`。

- [ ] **Step 1: 写失败测试**

```python
import polars as pl
from app.factor_research.audit import AuditService
from app.factor_research.policies import load_policy


def test_audit_detects_stale_and_cross_section(tmp_path):
    policy = load_policy(tmp_path)
    panel = pl.DataFrame({
        "date": ["2026-11-01", "2026-11-01", "2026-11-02", "2026-11-02"],
        "symbol": ["a", "b", "a", "b"],
        "factor_x": [1.0, 2.0, 2.0, 3.0],
    })
    result = AuditService(tmp_path, policy).audit_panel(panel, ["factor_x"])
    assert result.factors["factor_x"].cross_section_type == "stock_cross_sectional"
    assert result.factors["factor_x"].latest_date == "2026-11-02"
    assert result.audit_id.startswith("sha256:")


def test_five_x_is_diagnostic_only():
    policy = load_policy(tmp_path=None)
    assert policy.cost.diagnostic_5x.hard_rejection is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_audit.py -q
```

- [ ] **Step 3: 实现**

`audit_panel` 只做批量面板统计：

- 每因子按 date 计算非空数、唯一值数、industry 常量比例；
- 按默认窗口输出 `stock_cross_sectional`/`industry_cross_sectional`/`market_level`/
  `event_sparse`/`unknown`；
- 计算 latest non-null 和 staleness；
- 读取 taxonomy/PIT 状态；
- 生成分层 findings；
- 5× 只写 warning。

`load_policy(None)` 使用包内默认 policy；有 `data_dir` 时优先读
`data/factor_research/policies/default_v1.json`。

`scoring_adapter` 只转换 active alpha；`direction=low` 转有效负权重；非 alpha 角色不静默
丢弃，返回错误。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_audit.py -q
```

---

### Task 8: API 与路由接线

**Files:**
- Create: `backend/app/api/factor_research.py`
- Modify: `backend/app/main.py`（只新增 import 和 `include_router`）
- Test: `backend/tests/factor_research/test_api.py`

**Interfaces:**
- Produces:
  - `GET /api/factor-research/catalog/latest`
  - `GET /api/factor-research/catalog/{snapshot_id}`
  - `GET /api/factor-research/pools`
  - `GET /api/factor-research/pools/{pool_id}`
  - `GET /api/factor-research/sets`
  - `GET /api/factor-research/sets/{set_id}`
  - `POST /api/factor-research/pools/{pool_id}/revisions`
  - `POST /api/factor-research/sets/{set_id}/versions`
  - `POST /api/factor-research/sets/{set_id}/head`
  - `GET /api/factor-research/ledger`
  - `GET /api/factor-research/audits/{audit_id}`

- [ ] **Step 1: 写失败测试**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.factor_research import router


def test_catalog_latest_is_readable(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.repo = type("Repo", (), {"store": type("Store", (), {"data_dir": tmp_path})()})()
    client = TestClient(app)
    response = client.get("/api/factor-research/catalog/latest")
    assert response.status_code in {200, 503}
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_api.py -q
```

- [ ] **Step 3: 实现**

API 从 `request.app.state.repo.store.data_dir` 取数据目录；所有写请求必须在锁内重新解析
引用和计算指纹；错误使用明确 code：`invalid_request`、`not_found`、`version_conflict`、
`stale_reference`、`audit_required`、`unavailable`。不在请求线程执行全量回测。

- [ ] **Step 4: 跑测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_api.py -q
```

---

### Task 9: 初始种子、CLI 和 Git 追踪例外

**Files:**
- Create: `backend/app/factor_research/bootstrap.py`
- Create: `scripts/sync_factor_research_catalog.py`
- Create: `scripts/bootstrap_factor_research.py`
- Create: `scripts/audit_factor_research.py`
- Modify: `.gitignore`
- Create: `data/factor_research/catalog/field_taxonomy.json`
- Create: `data/factor_research/catalog/historical_aliases.json`
- Create: `data/factor_research/policies/default_v1.json`
- Create: `data/factor_research/policies/core_v1.json`
- Test: `backend/tests/factor_research/test_bootstrap.py`

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path
from app.factor_research.bootstrap import bootstrap_core_research


def test_core_seed_is_watch_only(tmp_path: Path):
    result = bootstrap_core_research(tmp_path)
    assert result.set_id == "core_v1"
    assert result.head.state == "watch"
    assert result.head.active_version is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_bootstrap.py -q
```

- [ ] **Step 3: 实现种子**

`bootstrap_core_research`：

1. 同步母集；
2. 创建五个研究池：`core_v1`、`financial_v1`、`sentiment_flow_v1`、`industry_v1`、
   `regime_v1`；
3. `core_v1` 的四条 alpha 成员为 watch，权重 0.35/0.15/0.30/0.20；
4. 退出信号为
   `uf_industry_relative_strength_change_5d`，角色 `exit_signal`，watch；
5. 创建 `core_v1` v1 watch head，`active_version=null`；
6. 记录一条 snapshot、pool revision、set version、head 和 seed 审计占位；重复运行必须
   幂等，不重复追加。

`.gitignore` 只增加：

```gitignore
!data/factor_research/
!data/factor_research/**
data/factor_research/cache/
data/factor_research/**/*.parquet
data/factor_research/**/*.tmp
data/factor_research/**/__pycache__/
```

- [ ] **Step 4: 跑 bootstrap 测试和脚本**

```bash
cd backend && uv run --frozen pytest tests/factor_research/test_bootstrap.py -q
cd .. && uv run --project backend python scripts/bootstrap_factor_research.py
```

预期：生成的 `core_v1` head 状态为 `watch`，台账无覆盖，`active_version` 为 null。

---

### Task 10: 总体验证和回归

**Files:**
- Test: `backend/tests/factor_research/`
- Existing regression: `backend/tests/test_factor_registry.py`、`test_factor_store.py`、
  `test_factor_api.py`

- [ ] **Step 1: 跑研究层测试**

```bash
cd backend && uv run --frozen pytest tests/factor_research -q
```

- [ ] **Step 2: 跑既有因子回归**

```bash
cd backend && uv run --frozen pytest tests/test_factor_registry.py tests/test_factor_store.py tests/test_factor_api.py -q
```

- [ ] **Step 3: 检查格式和差异**

```bash
cd backend && uv run --frozen ruff check app/factor_research app/api/factor_research.py tests/factor_research
cd .. && git diff --check
git status --short --branch
```

- [ ] **Step 4: 验收脚本**

```bash
cd .. && uv run --project backend python scripts/sync_factor_research_catalog.py
uv run --project backend python scripts/audit_factor_research.py --help
```

验收必须满足：

- 母集当前包含 223 个注册因子（测试 fixture 不写死 223）；
- `amount` 和 `consecutive_limit_ups` 是 physical column；
- `core_v1` 是 watch，active_version 为 null；
- 5× 没有硬拒绝路径；
- 旧 R359-R371 文件未被本任务改动；
- 不提交、不推送，等待用户单独确认。
