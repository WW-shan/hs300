# HS300 K-Line, Factor Research, and Backtest System Design

**Date:** 2026-09-28

**Status:** Approved for implementation planning

## Goal

Build a self-hosted HS300 research system that synchronizes point-in-time index constituents and OHLCV data, renders interactive K-lines with technical indicators, evaluates factors, runs A-share-aware backtests, and exposes results through a web UI.

## Architecture

```text
AKShare / Tushare
        |
Parquet + DuckDB
        |
FastAPI
        |
React + KLineChart + ECharts
        |
Qlib research/backtest jobs
```

The first release is a vertical slice: one data provider, daily bars, HS300 constituents, standard indicators, one factor-evaluation flow, and one benchmark backtest. Minute data, live trading, Tick storage, multi-user operation, and automated factor evolution are later phases.

## Reuse Policy

Prefer maintained packages over copied implementation code. This keeps security updates and upstream fixes available.

1. Use permissively licensed packages directly: Qlib (MIT), AKShare (MIT), Alphalens-reloaded (Apache-2.0), QuantStats (Apache-2.0), FastAPI, DuckDB, Polars, KLineChart (Apache-2.0), ECharts (Apache-2.0), and React.
2. Copy focused modules only when no suitable package boundary exists. Record the upstream repository, commit SHA, original path, license, and local modifications.
3. Do not copy GPL, AGPL, Commons Clause, or non-commercial code into the core. QuantMind, backtesting.py, backtrader, RQAlpha, and VectorBT are therefore not source-donation candidates for v1.
4. Add `THIRD_PARTY_NOTICES.md` before merging copied code.
5. Keep vendored code under `vendor/` or next to the adapter that owns it; do not copy complete upstream applications.

## Initial Code Donations

The following MIT-licensed project is the primary implementation reference:

- `shy3130/tick-stock-panel` at commit `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`.

Candidate donations, to be adapted rather than copied wholesale:

- `backend/app/data_providers/base.py`: provider protocol; replace TickFlow-specific assumptions.
- `backend/app/data_providers/schemas.py`: normalized OHLCV column contract.
- `frontend/src/components/CandlestickChart.tsx`: chart lifecycle, resize, theme, volume-pane patterns.
- Selected ECharts wrappers: navigation, drawdown, return distribution, and factor-correlation presentation.
- `backend/app/factors/dsl.py`: evaluate after the basic factor pipeline works; adapt the DSL only if its schemas and operators remain compatible.

Qlib examples may donate workflow YAML, but Qlib itself remains an installed dependency. KLineChart, ECharts, AKShare, Alphalens-reloaded, and QuantStats remain dependencies rather than vendored source.

## Milestone 1: Data and K-Line

- Store raw and adjusted daily OHLCV in Parquet; query through DuckDB.
- Synchronize HS300 constituents with effective dates to avoid survivorship bias.
- Expose health, universe, and daily-bar APIs through FastAPI.
- Calculate MA/EMA, VOL, MACD, KDJ, BOLL, and RSI once in the backend.
- Render K-line, volume, overlays, and indicator panes in React with KLineChart.
- Add deterministic unit tests using fixture data; never require the network in tests.

## Milestone 2: Factor Laboratory

- Define a factor interface returning `date`, `symbol`, and `value`.
- Add neutralization, winsorization, and standardization utilities.
- Produce IC, Rank IC, ICIR, quantile returns, long-short returns, turnover, and decay.
- Store factor metadata and results so the UI can compare runs.
- Render factor charts with ECharts.

## Milestone 3: Backtest

- Use Qlib for cross-sectional HS300 portfolio backtests.
- Model T+1, suspensions, price limits, commissions, stamp duty, and slippage.
- Rebalance against point-in-time HS300 membership.
- Export normalized metrics and time series for the UI.
- Add walk-forward and out-of-sample evaluation before presenting results.

## Directory Layout

```text
backend/app/api/             FastAPI routes
backend/app/data_providers/  Provider contracts and adapters
backend/app/factors/         Factor definitions and evaluation
backend/app/backtest/        Backtest orchestration and A-share rules
backend/app/indicators/      Technical indicator calculations
backend/tests/               Backend unit and integration tests
frontend/src/components/     K-line and analysis components
frontend/src/pages/          Universe, stock, factor, and backtest pages
research/qlib/               Qlib configuration and notebooks
docs/                        Architecture, operations, and data notes
vendor/                      Attributable copied source, when unavoidable
```

## Testing and Acceptance

- `pytest` covers providers, indicator correctness, factor calculations, and cost rules.
- `npm test` covers chart/data transformations and critical UI states.
- A clean checkout can start through Docker Compose with documented commands.
- The demo renders an HS300 stock's daily K-line with selectable indicators.
- A sample factor displays IC and quantile charts.
- A benchmark backtest includes costs and never reads future membership or prices.

## Risks

- Free data interfaces can change; provider adapters must normalize schema and fail explicitly.
- Duplicate indicator implementations can diverge; backend values are authoritative in v1.
- Qlib integration may require explicit A-share rule extensions and data conversion.
- Upstream code copying can create security and maintenance debt; dependency use is the default.
