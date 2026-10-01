# Upstream Reuse Analysis

**Date:** 2026-09-28

## Conclusion

Use `shy3130/tick-stock-panel` as the complete application base, `yfiua/index-constituents` as the HS300 point-in-time dataset, and `1nchaos/adata` as the market-data SDK. This gives the project a working FastAPI/React/Polars/DuckDB application, historical HS300 membership snapshots, and a maintained data source without rebuilding the system from scratch.

## Pinned Versions

| Source | License | Pin | Local use |
|---|---|---|---|
| `shy3130/tick-stock-panel` | MIT | `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39` | Complete application baseline; local HS300 changes stay at service/plugin/API/UI edges and focused PIT filters. |
| `yfiua/index-constituents` | Apache-2.0 | `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79` | Only CSI300 monthly snapshots and their update tooling are imported. |
| `1nchaos/adata` | Apache-2.0 | `adata==2.9.5` | Pinned runtime dependency; no AData source is copied into the repository. |

## Candidate Comparison

| Project | License | Stars | Last Push | Role | Verdict |
|---|---:|---:|---|---|---|
| `shy3130/tick-stock-panel` | MIT | 5,293 | 2026-09-28 | Complete A-share workbench: FastAPI, React, Polars, DuckDB, factors, backtests, plugins, CI, 2,400+ tests | **Selected as application base** |
| `yfiua/index-constituents` | Apache-2.0 | 63 | 2026-09-01 | Monthly CSI300 constituent snapshots since 2023-07 | **Selected for HS300 PIT data** |
| `1nchaos/adata` | Apache-2.0 | 5,256 | 2025-12-26 | A-share stock/index market SDK with adjusted daily bars | **Selected as data dependency** |
| `myhhub/stock` | Apache-2.0 | 14,666 | 2026-04-02 | Tornado + MySQL + crawler stock system, 200+ factors, no visible test suite | Not selected as base |
| `ling-0729/KHunter` | MIT | 603 | 2026-09-13 | Flask + SQLite + AKShare strategy system, one test file | Not selected as base |
| `hello245m/free-stockdb` | MIT | 2,710 | 2026-09-08 | Local C++ market database and 39 indicators | Optional performance engine later |
| `microsoft/qlib` | MIT | 48,988 | 2026-09-22 | AI quant research and backtest platform | Optional research engine later |
| `fasiondog/hikyuu` | Apache-2.0 | 3,532 | 2026-09-27 | High-performance C++/Python backtest framework | Optional engine later |
| `vnpy/vnpy` | MIT | 45,607 | 2026-09-13 | Live trading platform | Out of scope for v1 |
| `liihuu/KLineChart` | Apache-2.0 | 4,173 | 2026-09-18 | K-line chart library | Not needed because upstream already uses `lightweight-charts` |

## Why `tick-stock-panel`

- It already has the target architecture: FastAPI backend, React frontend, Polars + DuckDB storage, plugin data sources, factor DSL, backtest engine, and Docker deployment.
- It has the strongest test posture among the whole-application candidates.
- Its plugin system allows adding AData without rewriting storage, services, or UI.
- Its existing screener/factor/backtest paths accept symbol pools; the HS300 preset loads the date-range union and uses the existing backtest services with daily snapshot masks rather than creating a parallel evaluation framework.

## Why `index-constituents`

- It provides monthly CSI300 snapshots from 2023-07 onward.
- Monthly snapshots are sufficient for a point-in-time v1: query the latest snapshot on or before a target date.
- It is Apache-2.0 and regularly updated by GitHub Actions.

Limitation:

- History begins in 2023-07. Backtests before that date must either be excluded or backfilled from another licensed source.

## Why `adata`

- It is a maintained Apache-2.0 SDK rather than a scraping script.
- It exposes stock metadata and daily bars. The builtin provider requests unadjusted raw prices (`adjust_type=0`) so the existing routed `adj_factor` + enriched pipeline remains the single forward-adjustment owner.
- It is easier to pin and test than copying a crawler from another application.

## Reuse Boundary

Copy now:

1. `tick-stock-panel` runtime, tests, CI, Docker, frontend, and backend.
2. `index-constituents` monthly CSI300 CSV snapshots and update scripts.

Use as pinned dependencies:

1. `adata==2.9.5`.

Defer:

1. Qlib integration.
2. `free-stockdb` local engine.
3. Hikyuu or vn.py.
4. Any GPL/AGPL or license-ambiguous project.
