# HS300 Upstream-Derived Research System Design

**Date:** 2026-09-28

**Status:** Approved for implementation planning (历史记录) — 本设计定稿时市场数据源为 AData；实现已于 2026-10-07 切换为 AkShare，当前数据源契约见 `AGENTS.md` 与 `docs/upstream-reuse-analysis.md`。以下内容保留原始设计上下文，不得据其中的 AData 依赖描述恢复实现。

## Goal

Build a self-hosted HS300 research system by importing a complete open-source A-share workbench, adding point-in-time HS300 membership, wiring a maintained market-data SDK, and exposing HS300 presets through the existing factor, K-line, and backtest UI.

## Architecture

```text
index-constituents (CSI300 snapshots)
        |
adata SDK + tick-stock-panel plugin system
        |
tick-stock-panel backend (FastAPI + Polars + DuckDB)
        |
tick-stock-panel frontend (React + lightweight-charts + ECharts)
```

The implementation starts by importing `tick-stock-panel` at commit `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`, then adds HS300-specific data and presets. The first release is an upstream-derived vertical slice: existing panel/K-line/factor/backtest infrastructure, HS300 constituent history, daily bars, standard indicators, one factor-evaluation flow, and one benchmark backtest. Minute data, live trading, Tick storage, multi-user operation, and automated factor evolution remain later phases.

## Reuse Policy

Source reuse is the primary strategy. Import a complete working application first, then make the smallest HS300-specific changes. Use maintained packages for libraries that are not part of the application shell.

1. Import `shy3130/tick-stock-panel` at commit `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39` as the application base.
2. Import `yfiua/index-constituents` at commit `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79` for CSI300 snapshots.
3. Use `adata==2.9.5` as the market-data SDK.
4. Record every upstream repository, commit SHA, license, imported path, and local modification.
5. Do not copy GPL, AGPL, Commons Clause, or non-commercial code into the core.
6. Add `THIRD_PARTY_NOTICES.md` before merging imported code.
7. Preserve upstream copyright and license notices; omit sponsor branding and non-runtime screenshot assets.

## Initial Code Donations

The following MIT-licensed project is imported as the application base:

- `shy3130/tick-stock-panel` at commit `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`.

Imported modules include:

- `backend/`: FastAPI application, provider contracts, storage, indicators, factors, backtest orchestration, services, and tests.
- `frontend/`: React application, stock table, charts, factor/backtest pages, and theme/layout.
- `Dockerfile`, `docker-compose.yml`, `dev.sh`, `dev.ps1`, CI workflows, and documentation.

The upstream chart implementation uses `lightweight-charts`; keep it for maximum reuse. Do not rewrite the frontend around `klinecharts` in v1.

Required HS300 adaptations:

Import only the CSI300 files and update tooling from `yfiua/index-constituents` under `vendor/index-constituents/`. Do not import its unrelated indices or Git metadata.

- Add a point-in-time HS300 membership service that reads those monthly CSV snapshots.
- Map snapshot symbols from Yahoo-style `.SS` to panel-style `.SH`; keep `.SZ` unchanged.
- Add an `adata` builtin provider that implements the upstream provider/plugin contract for instruments and daily bars.
- Add an HS300 universe selector and as-of membership endpoint to the existing pages and APIs.
- Keep TickFlow and all other upstream providers available; make `adata` the default for HS300 daily-bar synchronization.
- Rename deployment and product labels without altering the core architecture.

`adata==2.9.5` is an installed Apache-2.0 dependency. Do not vendor or rewrite AData. Qlib, Hikyuu, vn.py, and other research/trading engines remain deferred.

## Milestone 1: Data and K-Line

- Import the existing Parquet/DuckDB repository, FastAPI routes, indicator engine, and React chart stack.
- Add HS300 snapshot loading with effective month dates to avoid survivorship bias.
- Reuse `kline_sync.sync_and_persist_daily_batch` and the upstream repository's atomic Parquet upserts.
- Reuse existing health, daily-bar, factor, and backtest APIs.
- Add an HS300 universe selector, snapshot browser, and default dashboard focus.
- Preserve deterministic upstream tests and add HS300 membership/provider tests using fixture data.

## Milestone 2: Factor Laboratory

- Reuse the upstream factor DSL, registry, trial endpoint, and factor backtest service.
- Add an HS300 preset that resolves the exact snapshot members for the selected as-of date and passes them through the existing `symbols` parameter.
- Expose at least one existing momentum/reversal factor as the HS300 demo.
- Preserve upstream IC, Rank IC, ICIR, quantile return, turnover, and ECharts reporting.
- Do not create a parallel factor framework.

## Milestone 3: Backtest

- Reuse the upstream strategy and factor backtest engines; do not add Qlib in v1.
- Reuse existing T+1 fill handling, suspension/price-limit awareness, commissions, stamp duty, and slippage configuration.
- Add an HS300 backtest preset that loads the union of members over the requested range, then applies date-by-date membership filtering before scoring and execution.
- Export normalized metrics and time series through the existing backtest APIs and UI.
- Reuse the upstream walk-forward service only after the point-in-time filter is covered by regression tests.

## Directory Layout

```text
backend/app/api/             FastAPI routes
backend/app/data_providers/  Provider contracts and adapters
backend/app/factors/         Factor definitions and evaluation
backend/app/backtest/        Backtest orchestration and A-share rules
backend/app/indicators/      Technical indicator calculations
backend/tests/               Backend unit and integration tests
backend/app/plugins/adata/   AData builtin provider plugin
frontend/src/components/     K-line and analysis components
frontend/src/pages/          Universe, stock, factor, and backtest pages
docs/                        Architecture, operations, and data notes
vendor/index-constituents/   CSI300 snapshots and licensed update tooling
```

## Testing and Acceptance

- `pytest` covers providers, indicator correctness, factor calculations, and cost rules.
- `pnpm build` and focused Vitest tests cover HS300 UI state and API-client transformations.
- A clean checkout can start through Docker Compose with documented commands.
- The demo renders an HS300 stock's daily K-line with selectable indicators.
- A sample factor displays IC and quantile charts.
- A benchmark backtest includes costs and never includes a stock outside its snapshot membership on a given date.

## Risks

- CSI300 snapshots begin in 2023-07; requests before that date must return an explicit error rather than silently using the first snapshot.
- AData's free endpoints can change; the provider must normalize schema and fail visibly.
- Upstream imports create maintenance debt; record the exact commit and keep local changes in small HS300-specific modules.
- Static `symbols` lists are not sufficient for historical backtests; date-by-date membership filtering is required before execution.
