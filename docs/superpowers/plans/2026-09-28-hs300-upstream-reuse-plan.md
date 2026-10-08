# HS300 Upstream-Reuse Implementation Plan

> **历史实施记录（已归档）**: 本计划记录 AData 数据源时期的实施步骤与当时验证结果；实现已于 2026-10-07 切换为 AkShare（commit 596f7c8）。当前数据源、依赖与验证口径以 `AGENTS.md`、`docs/upstream-reuse-analysis.md`、`backend/pyproject.toml` 为准，不要按本文中的 AData 步骤执行。另：2026-10-08 起 HS300 成分改为中证指数官方接口实时解析的当前名单（动态筛选器），`index-constituents` 月度快照/PIT 机制已删除，本文中的快照步骤同样不再适用；同日删除了 `/api/hs300/sync` 接口与 UI「同步日K」入口，行情统一由全市场日K管线同步。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import a complete open-source A-share workbench and add a correct HS300 point-in-time vertical slice with AData daily bars, factor evaluation, and backtest presets.

**Architecture:** Copy `shy3130/tick-stock-panel` at a pinned MIT commit as the application base. Import CSI300 monthly snapshots from `yfiua/index-constituents`, use `adata==2.9.5` through the existing provider-plugin contract, and keep all K-line, indicator, factor, and backtest flows in upstream code. Local development adds only membership, AData adaptation, HS300 API/preset, and PIT filtering.

**Tech Stack:** Python 3.12, FastAPI, Polars, DuckDB, Parquet, pytest, Ruff, React, TypeScript, Vite, `lightweight-charts`, ECharts, and AData 2.9.5.

---

## Reuse Contract

1. Import the complete upstream application before writing HS300-specific code.
2. Do not reimplement provider contracts, storage, indicators, charts, factors, backtests, Docker, CI, or tests.
3. Prefer changing configuration or passing existing parameters (`pool`, `symbols`, `as_of`) over adding parallel systems.
4. New handwritten production code is limited to these boundaries:
   - `backend/app/hs300/`
   - `backend/app/plugins/adata/`
   - `backend/app/api/hs300.py`
   - focused PIT backtest filtering in the existing backtest path
   - HS300 frontend preset/hook and API-client methods
5. If a needed capability already exists upstream, use it and add a test rather than creating a replacement module.

## Upstream Pins

| Source | License | Exact pin | Reuse |
|---|---|---|---|
| `shy3130/tick-stock-panel` | MIT | `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39` | Complete backend, frontend, tests, Docker, CI, and docs |
| `yfiua/index-constituents` | Apache-2.0 | `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79` | CSI300 CSV snapshots and update scripts |
| `1nchaos/adata` | Apache-2.0 | PyPI `2.9.5` | Installed SDK for instruments and adjusted daily bars |

## File Map

```text
THIRD_PARTY_NOTICES.md
AGENTS.md
README.md
backend/app/hs300/__init__.py
backend/app/hs300/service.py
backend/app/api/hs300.py
backend/app/plugins/adata/__init__.py
backend/app/plugins/adata/provider.py
backend/app/plugins/adata/plugin.yaml
backend/tests/test_hs300_service.py
backend/tests/test_adata_provider.py
backend/tests/test_hs300_api.py
backend/tests/test_hs300_backtest_membership.py
frontend/src/lib/api.ts
frontend/src/hooks/useHS300Universe.ts
frontend/src/components/hs300/UniverseSelector.tsx
frontend/src/pages/Screener.tsx
frontend/src/pages/Factors.tsx
frontend/src/pages/Backtest.tsx
vendor/index-constituents/LICENSE
vendor/index-constituents/README.md
vendor/index-constituents/requirements.txt
vendor/index-constituents/get-constituents.py
vendor/index-constituents/get-constituents-historical.py
vendor/index-constituents/update-monthly.sh
vendor/index-constituents/docs/**/constituents-csi300.csv
```

### Task 1: Import the pinned application and datasets

**Files:**
- Import: all runtime, test, CI, Docker, backend, and frontend files from `tick-stock-panel`
- Import: CSI300 snapshots and update tooling from `index-constituents`
- Create: `THIRD_PARTY_NOTICES.md`
- Modify: `AGENTS.md`, `README.md`

Set `TICK_STOCK_PANEL_DIR`, `INDEX_CONSTITUENTS_DIR`, and `ADATA_SOURCE_DIR` to the local checkout directories at the exact pins above before running the import commands below.

- [x] **Step 1: Verify the three pins before copying**

```bash
git -C "$TICK_STOCK_PANEL_DIR" rev-parse HEAD
git -C "$INDEX_CONSTITUENTS_DIR" rev-parse HEAD
rg '"__version__"|VERSION =' "$ADATA_SOURCE_DIR/adata/__version__.py"
```

Expected values are respectively `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`, `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79`, and `2.9.5`.

- [x] **Step 2: Import the application without upstream Git metadata**

```bash
rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'node_modules/' \
  --exclude 'gui-test-screenshots/' \
  --exclude 'community-qr-code.jpg' \
  "$TICK_STOCK_PANEL_DIR"/ ./
```

Keep the upstream `LICENSE`, source headers, test suite, `backend/pyproject.toml`, `frontend/pnpm-lock.yaml`, Dockerfile, Compose file, and CI workflows.

- [x] **Step 3: Import only CSI300 data and its update tooling**

```bash
mkdir -p vendor/index-constituents
rsync -a \
  "$INDEX_CONSTITUENTS_DIR/LICENSE" \
  "$INDEX_CONSTITUENTS_DIR/README.md" \
  "$INDEX_CONSTITUENTS_DIR/requirements.txt" \
  "$INDEX_CONSTITUENTS_DIR/get-constituents.py" \
  "$INDEX_CONSTITUENTS_DIR/get-constituents-historical.py" \
  "$INDEX_CONSTITUENTS_DIR/update-monthly.sh" \
  vendor/index-constituents/

rsync -a \
  --include '*/' \
  --include 'constituents-csi300.csv' \
  --exclude '*' \
  "$INDEX_CONSTITUENTS_DIR/docs/" \
  vendor/index-constituents/docs/
```

- [x] **Step 4: Record legal provenance and commands**

`THIRD_PARTY_NOTICES.md` must contain each upstream repository URL, commit, license, imported path, and local modification summary. Update `AGENTS.md` with:

```text
Backend: cd backend && uv sync --extra dev --frozen && uv run pytest tests -q
Frontend: cd frontend && pnpm install --frozen-lockfile && pnpm build
Focused frontend test: cd frontend && pnpm vitest run src/<test-file>
Whitespace check: git diff --check
```

Update `README.md` with setup, HS300 snapshot limitations, AData configuration, sync, and Docker Compose commands.

- [x] **Step 5: Establish the imported baseline**

```bash
cd backend && uv sync --extra dev --frozen && uv run pytest tests -q
cd ../frontend && pnpm install --frozen-lockfile && pnpm build
```

Expected: upstream tests and builds pass before any HS300 code is changed. If an unrelated upstream test fails in this environment, record the exact failure and stop rather than patching upstream behavior.

### Task 2: Add the point-in-time membership service

**Files:**
- Create: `backend/app/hs300/__init__.py`
- Create: `backend/app/hs300/service.py`
- Test: `backend/tests/test_hs300_service.py`

- [x] **Step 1: Write deterministic snapshot tests first**

Create two fixture snapshots under a temporary directory:

```python
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.hs300.service import HS300MembershipError, HS300Service


def _snapshot(root: Path, year: int, month: int, symbols: list[str]) -> None:
    path = root / "docs" / f"{year:04d}" / f"{month:02d}" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"Symbol": symbols, "Name": [f"stock-{i}" for i in range(len(symbols))]}).write_csv(path)


def test_members_select_latest_snapshot_on_or_before_date(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS", "000001.SZ"])
    _snapshot(tmp_path, 2023, 8, ["600519.SS", "300750.SZ"])
    service = HS300Service(tmp_path / "docs")

    frame = service.members(date(2023, 7, 31))

    assert frame["symbol"].to_list() == ["600519.SH", "000001.SZ"]
    assert frame["effective_date"].to_list() == [date(2023, 7, 1), date(2023, 7, 1)]


def test_dates_before_first_snapshot_fail_explicitly(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS"])
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="CSI300 snapshots begin"):
        service.members(date(2023, 6, 30))
```

- [x] **Step 2: Implement the service by reading the imported CSVs**

The public behavior is:

- `HS300Service(snapshot_root: Path)`
- `snapshot_dates() -> list[date]`
- `members(as_of: date) -> pl.DataFrame`
- `members_between(start: date, end: date) -> pl.DataFrame`

Requirements:

- Snapshot effective dates are the first day of the path month.
- `members(as_of)` chooses the latest snapshot on or before `as_of`.
- `.SS` becomes `.SH`; `.SZ` and `.BJ` remain unchanged.
- Returned columns are `symbol`, `name`, `effective_date`, `snapshot_date`, `source`.
- `members_between` returns the union with per-row effective/snapshot dates.
- Any request before 2023-07 raises `HS300MembershipError`; it must not silently fall back to the first snapshot.
- The service reads only local CSV files and performs no network access.

- [x] **Step 3: Run the focused test**

```bash
cd backend && uv run pytest tests/test_hs300_service.py -q
```

Expected: all tests pass without network access.

### Task 3: Add the AData builtin provider

**Files:**
- Create: `backend/app/plugins/adata/__init__.py`
- Create: `backend/app/plugins/adata/provider.py`
- Create: `backend/app/plugins/adata/plugin.yaml`
- Modify: `backend/pyproject.toml`
- Modify: `packaging/tickflow.spec`
- Modify: `.github/workflows/release.yml`
- Test: `backend/tests/test_adata_provider.py`

- [x] **Step 1: Add the pinned dependency**

Add `"adata==2.9.5"` to the backend base dependencies and run:

```bash
cd backend && uv lock && uv sync --extra dev --frozen
```

- [x] **Step 2: Write provider tests with a fake AData SDK**

Cover these behaviors without network access:

- `test_daily_maps_symbols_and_schema`
- `test_iter_daily_yields_bounded_normalized_symbol_batches`
- `test_instruments_use_panel_suffixes`
- `test_unsupported_datasets_return_empty_without_error`
- `test_adata_failure_is_not_swallowed_as_success`

Assertions must include:

- AData six-digit code `600519` is requested for panel symbol `600519.SH`.
- `trade_time` becomes `date`.
- Output daily columns include `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, and `amount`.
- `iter_daily` yields normalized batches of at most 50 symbols, bounding staging file fan-out while reusing the existing streamed repository write path.
- Unwrapped AData exceptions propagate. Since `adata==2.9.5` can convert request failures into empty frames, `zero_row_out` reports missing symbols and an all-zero HS300 sync fails explicitly instead of returning success.
- AData supplies volume in shares; the provider converts it at the boundary to the project contract's lots (`手`).

Use these exact SDK boundaries in the fake:

```python
adata.stock.info.all_code()
adata.stock.market.get_market(
    stock_code="600519",
    start_date="2023-07-03",
    end_date="2023-07-07",
    k_type=1,
    adjust_type=0,
)
```

- [x] **Step 3: Implement the upstream plugin contract**

`plugin.yaml` must declare:

```yaml
name: adata
display_name: "AData"
runtime: none
entry: app.plugins.adata.provider:ADataProvider
check: app.plugins.adata.provider:availability
datasets: [daily]
description: "AData A股股票列表与不复权原始日K数据(前复权由复权因子源统一处理)。"
homepage: "https://github.com/1nchaos/adata"
```

The provider class is `ADataProvider`, with `name = "adata"` and `builtin = True`. It implements:

- `get_instruments(asset_type="stock") -> list[dict]`
- `iter_daily(symbols, start_time, end_time, on_chunk_done=None)`
- `get_daily(symbols, start_time, end_time, asset_type="stock", on_chunk_done=None) -> pl.DataFrame`
- `get_adj_factors(*args, **kwargs) -> pl.DataFrame`
- `get_minute(*args, **kwargs) -> pl.DataFrame`
- `get_realtime(*args, **kwargs) -> pl.DataFrame`
- `get_depth_batch(symbols) -> dict[str, dict]`

`get_instruments` follows the upstream plugin path used by `instrument_sync._fetch_instruments_via_provider`: return flat dictionaries with `symbol`, `name`, `code`, `exchange`, `region`, and `type`. Map AData's `stock_code`/`short_name`/`exchange` columns to those fields and use panel suffixes (`600519.SH`).

Reuse `app.data_providers.normalizer.normalize_daily` for bars; do not write another bar normalizer. The daily table is the raw-price layer (`adjust_type=0`): forward adjustment is owned by the enriched pipeline and its separately routed `adj_factor` provider. AData has no adjustment-factor dataset in v1, so it stays undeclared and returns an empty frame instead of fabricating factors.

- [x] **Step 4: Register and verify through upstream loading**

```python
def test_adata_plugin_is_available(monkeypatch):
    from app.data_providers import custom

    custom.load_all()
    names = custom.names()

    assert "adata" in names
```

Run:

```bash
cd backend && uv run pytest tests/test_adata_provider.py -q
```

Expected: provider tests and plugin registration pass without network calls.

The PyInstaller bundle also includes the AData manifest, provider module, and SDK package data so the dynamic builtin-plugin loader works in frozen desktop builds.

### Task 4: Expose HS300 APIs and synchronization

**Files:**
- Create: `backend/app/api/hs300.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_hs300_api.py`

- [x] **Step 1: Define the API contract**

Add three routes on `APIRouter(prefix="/api/hs300")`:

```text
GET  /snapshots                 -> {"snapshots": ["2023-07-01", "2023-08-01"], "earliest": "2023-07-01", "latest": "2023-08-01"}
GET  /members?as_of=2023-07-31 -> {"as_of": "2023-07-31", "snapshot_date": "2023-07-01", "members": [{"symbol": "600519.SH", "name": "贵州茅台"}]}
POST /sync                      -> {"ok": true, "symbols": 300, "rows": 198000, "start": "2023-07-03", "end": "2026-09-26"}
```

`POST /sync` accepts:

```json
{"start": "2023-07-01", "end": "2026-09-26", "provider": "adata"}
```

Behavior:

- `start` defaults to the earliest snapshot.
- `end` defaults to the latest snapshot.
- Reject ranges that begin before the first snapshot with HTTP 422.
- Resolve `members_between(start, end)` and pass the union to `sync_and_persist_daily_batch`.
- Pass the requested provider as a single-call override to the existing sync function; do not mutate global preferences while network requests are in flight.
- Return the upstream written-row count, symbol count, and `zero_row_symbols`. If the entire batch writes zero rows, return HTTP 502 with provider/range/zero-row details rather than `ok: true`.
- Use a short-lived state lock only to mark/reject concurrent HS300 sync requests; do not hold a mutex during provider network access.

- [x] **Step 2: Test API behavior with a fake repository and provider**

Use FastAPI `TestClient`, temporary snapshots, and a monkeypatched sync function. Assert:

- members endpoint returns normalized `.SH` symbols;
- before-first-snapshot returns HTTP 422;
- sync calls the upstream sync function with exactly the union of symbols;
- the global daily-provider preference is unchanged on success and failure;
- the lock is not held while the upstream sync function performs network I/O, and concurrent HS300 requests return HTTP 409;
- empty SDK frames are surfaced as zero-row results and an all-zero sync fails explicitly;
- no test reaches AData or the network.

- [x] **Step 3: Register the router and run focused tests**

Import `hs300` in `backend/app/main.py` alongside existing API modules and add `app.include_router(hs300.router)`.

```bash
cd backend && uv run pytest tests/test_hs300_api.py tests/test_openapi_contract.py -q
```

Expected: focused tests pass; the upstream OpenAPI snapshot is updated only for the new `/api/hs300` routes.

### Task 5: Add the HS300 frontend universe preset

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/hooks/useHS300Universe.ts`
- Create: `frontend/src/components/hs300/UniverseSelector.tsx`
- Modify: `frontend/src/pages/Screener.tsx`
- Modify: `frontend/src/pages/Factors.tsx`
- Modify: `frontend/src/pages/Backtest.tsx`

- [x] **Step 1: Add typed API-client methods**

Add:

```typescript
hs300Snapshots: () => request<{ snapshots: string[]; earliest: string; latest: string }>('/api/hs300/snapshots'),
hs300Members: (asOf: string) => request<HS300MembersResponse>(`/api/hs300/members?as_of=${encodeURIComponent(asOf)}`),
hs300Sync: (start: string, end: string) => request<HS300SyncResponse>('/api/hs300/sync', {
  method: 'POST',
  body: JSON.stringify({ start, end, provider: 'adata' }),
}),
```

Define `HS300MembersResponse` and `HS300SyncResponse` next to the existing API types; do not use `any`.

- [x] **Step 2: Create one reusable universe hook**

> 实现注记: 前端无 `src/hooks/` 目录, hook 落在 `frontend/src/lib/useHS300Universe.ts` (仓库既有 hook 约定); 另补 `GET /api/hs300/members-between?start=&end=` 供回测取区间并集。未增加一次薄包装 `applyTo(page)`：Screener、因子、策略页分别将同一个 `symbols` 结果直接映射到既有 `pool`/`symbols` 表单；回测的 PIT 模式直接消费区间 union。

`useHS300Universe` must:

- load snapshots once through React Query;
- expose `selectedDate`, `setSelectedDate`, `members`, `isLoading`, and `error`;
- default `selectedDate` to the latest snapshot;
- expose `applyTo(page)` helpers that return the symbol list for Screener and the `symbols` payload for Factors/Backtest;
- keep one shared cache key so switching pages does not refetch the same snapshot.

- [x] **Step 3: Add the selector without replacing upstream pages**

`UniverseSelector` is a compact dropdown/button group with:

- snapshot date select;
- member count;
- loading and error states;
- an `Apply HS300` action;
- a `Sync all daily bars` action and an explicit notice for dates before 2023-07.

Mount it in the existing Screener, Factors, and Backtest pages. Pass the selected members to the existing payload fields:

- Screener custom runs use the existing `pool` argument.
- Screener preset runs use the existing `pool` argument.
- Factor backtests load the union of members over the requested range and set `hs300: true`; the existing `symbols` field remains the data-load boundary.
- Strategy backtests load the union of members over the requested range and set `hs300: true`; the existing `symbols` field remains the data-load boundary.

Do not fork or rewrite `CandlestickChart`, `StockDailyKChart`, factor tables, or backtest result components.

- [x] **Step 4: Verify the frontend**

```bash
cd frontend && pnpm build
```

Add focused Vitest coverage for the hook and selector using the upstream test setup. Expected: TypeScript build passes and no test performs network access.

### Task 6: Add PIT membership filtering to backtests

**Files:**
- Modify: `backend/app/backtest/factor.py`
- Modify: `backend/app/backtest/strategy.py`
- Modify: `backend/app/backtest/matrix.py`
- Modify: `backend/app/api/backtest.py`
- Test: `backend/tests/test_hs300_backtest_membership.py`
- Test: `backend/tests/test_hs300_factor_membership.py`

- [x] **Step 1: Write a regression test that fails with a static union**

Construct a small panel with:

- stock `A` in HS300 on 2024-01-02 but not on 2024-01-03;
- stock `B` not in HS300 on 2024-01-02 but in HS300 on 2024-01-03;
- both present in the union passed through `symbols`;
- factor scores that would select both without PIT filtering.

Assert each execution date only permits the member on that date. This test must fail before the filter is added.

- [x] **Step 2: Add a membership filter at the existing panel boundary**

> 实现注记: 不通过请求传递 `(date, symbol)` 整表 (HTTP/worker JSON 体积与序列化成本), 而是在策略/因子配置增加服务端布尔位 `hs300`; 服务由 `HS300Service` 按 `[start, end]` 自行解析快照并构造 PIT 成分。策略 matrix 路径经 `MatrixPipelineConfig.asset_mask` 在评分前过滤, 面板路径 AND 进候选掩码; 因子路径在计算完整价格轴前瞻收益后、IC/分层/多空评分前过滤。策略矩阵签名包含该配置位。

Add an optional `hs300` flag to existing strategy and factor backtest configs. Resolve membership locally and apply the date-by-symbol mask at the existing panel/matrix boundary. Default behavior remains unchanged when the flag is absent or false.

```text
panel = panel.join(hs300_membership, on=["date", "symbol"], how="inner")
```

Use a left join plus null filtering if preserving extra panel columns is clearer. Apply the filter before scoring, position sizing, and fill simulation. Keep the default behavior unchanged when membership is not supplied.

- [x] **Step 3: Wire the HS300 backtest preset**

The strategy and factor HS300 API/frontend presets must:

1. request the union for data loading;
2. materialize `(date, symbol)` membership rows from all covered snapshots;
3. apply the date-by-symbol membership mask inside the upstream backtest service (for factor studies, after forward returns are materialized);
4. include the membership range in the response metadata.

- [x] **Step 4: Run focused and upstream backtest tests**

```bash
cd backend && uv run pytest tests/test_hs300_backtest_membership.py tests/test_factor_api.py tests/backtest -q
```

Expected: PIT regression passes and existing upstream backtests remain unchanged when no membership frame is supplied.

### Task 7: Documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md` to prevent using the upstream-only GHCR image for this derivative
- Modify: `docs/upstream-reuse-analysis.md`
- Modify: `THIRD_PARTY_NOTICES.md`
- Modify: `.env.example`
- Modify: `packaging/tickflow.spec` and `.github/workflows/release.yml` for frozen AData availability checks
- Modify: `docker-compose.yml` only if AData configuration requires it

- [x] **Step 1: Document the reuse-first operating model**

> 本轮更新: README 和上游复用分析记录项目阶段、精确 pins/许可证、provider 原始价与单位、快照边界、同步/API 和策略/因子 PIT 语义；THIRD_PARTY_NOTICES 记录本地实现和 frozen AData 打包；`.env.example` 增补可选快照目录。

Document:

- exact upstream pins and licenses;
- import boundary;
- AData as the per-request default provider for HS300 daily synchronization (without changing global provider preferences);
- snapshot coverage beginning 2023-07;
- one-command local setup;
- HS300 sync command and API examples;
- PIT semantics for screener, factor, and backtest;
- how to refresh `vendor/index-constituents`.

- [x] **Step 2: Run the full verification matrix**

> 最终验证（2026-09-29）: `uv sync --extra dev --frozen` 与前端 frozen install 通过；后端 `pytest tests -q` 为 **2520 passed, 7 skipped, 163 warnings**。全树 Ruff 退出 1，报告 **1577** 个上游存量 findings；将 20 个改动/新增后端 Python 文件与 pinned 上游逐行对照，新增或修改行 Ruff findings 为 0，新建 HS300/AData 文件的定向 Ruff 全通过。三个 HS300 Vitest 文件 **11 passed**；`pnpm build` 通过，仅有 ECharts chunk > 500 kB 的既有构建提示。同步按钮文案明确该动作截止最新快照生效日，避免误解为追到当日行情。
>
> 桌面包在临时 dist/work 目录 PyInstaller 6.22.3 fresh build 通过；产物包含 AData plugin manifest/provider/SDK `code.csv`、40 份 CSI300 CSV，打包前端 index 与本轮 `frontend/dist/index.html` 哈希一致。独立 frozen smoke 执行显示 AData 插件 available，SDK `code.csv` 有 5640 行。未联网调用真实 AData，也未启动 Docker Compose 或 GUI 手工验收。

```bash
cd backend && uv sync --extra dev --frozen
cd backend && uv run pytest tests -q
cd backend && uv run ruff check app tests
cd frontend && pnpm install --frozen-lockfile
cd frontend && pnpm build
git diff --check
```

Expected: backend tests, frozen frontend installation/build, focused HS300 Vitest tests, and whitespace checks pass. Full-tree Ruff results are reported separately from changed-file Ruff checks because imported upstream warnings must not trigger unrelated cleanup. Tests must not require network access or real exchange credentials.

- [x] **Step 3: Run a smoke test against a fixture dataset**

> 验证注记: 三标的/两快照 fixture 通过 FastAPI factor endpoint 与策略回测 API/worker 接线测试验证逐日成员结果；`/api/hs300/sync` 另通过真实 ADataProvider (SDK 假响应) + KlineRepository 验证规范化写入、成交量单位转换和空响应 fail-closed。追加覆盖策略 SSE cancel key 对齐、多种 FastAPI 布尔写法、空白快照目录回退、回测 PIT 元数据和仅表头快照 fail-closed。

Create or reuse a small deterministic fixture with three symbols and two snapshots. Verify through the API:

1. `/api/hs300/snapshots` returns both dates;
2. `/api/hs300/members` returns the exact PIT members;
3. sync writes normalized rows through the upstream repository;
4. the factor endpoint returns a result only for snapshot members;
5. the backtest response never contains a non-member execution.

- [x] **Step 4: Final provenance and placeholder audit**

> `rg 'TODO|TBD|implement later'` 在 README/specs/backend/frontend 无命中；`rg 'AKShare|klinecharts|research/qlib|pyqlib'` 在 README/backend/frontend 无命中。README 与部署指南的自有 CI/仓库链接已指向本项目；GHCR 说明明确不要拉取缺少 HS300 能力的上游镜像。40 份 `constituents-csi300.csv` 之外没有其他指数 CSV。

```bash
rg -n 'TO''DO|TBD|implement later' README.md docs/superpowers/specs backend frontend
rg -n 'AKShare|klinecharts|research/qlib|pyqlib' README.md backend frontend
git diff --check
```

Expected: no stale plan references, no placeholder language, no Qlib/KLineChart scope creep, and complete third-party provenance.

## Self-Review Checklist

- [x] Complete `tick-stock-panel` application imported at the exact pinned commit (900 upstream files audited; no missing files after excluding generated caches).
- [x] CSI300 snapshots and update tooling imported at the exact pinned commit (46 allowlisted vendor files, unchanged; 40 CSI300 CSVs and no other index CSVs).
- [x] AData is a pinned dependency, not copied source.
- [x] Requests before 2023-07 fail explicitly.
- [x] AData provider follows the upstream plugin contract and reuses upstream normalizers.
- [x] Screener and factor APIs reuse existing `pool`/`symbols` parameters.
- [x] Backtest uses date-by-date membership, not a static union.
- [x] No parallel storage, indicator, chart, factor, or backtest framework is introduced.
- [x] Full backend tests, changed-code lint, frontend build, focused frontend tests, and whitespace checks pass; full-tree Ruff has 1577 inherited findings and no findings on locally changed lines.
