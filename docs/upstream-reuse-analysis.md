# Upstream Reuse Analysis

**Date:** 2026-09-28

> **更新 2026-10-08**：HS300 成分已不再使用 `yfiua/index-constituents` 月度快照，改为调用中证指数官方接口实时解析**当前成分**（动态筛选器，无固定 300 池、无 PIT 归档）。下文中与固定快照数据集有关的选择、理由与复用边界仅作历史决策记录，不再代表当前实现。

## Conclusion

Use `shy3130/tick-stock-panel` as the complete application base and `akfamily/akshare` as the market-data SDK. This gives the project a working FastAPI/React/Polars/DuckDB application plus a maintained data source without rebuilding the system from scratch. HS300 membership comes from the official CSI index endpoint at query time (dynamic current filter); no fixed pool or snapshot dataset is vendored (decision updated 2026-10-08).

## Pinned Versions

| Source | License | Pin | Local use |
|---|---|---|---|
| `shy3130/tick-stock-panel` | MIT | `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39` | Complete application baseline; local HS300 changes stay at service/plugin/API/UI edges and focused PIT filters. |
| `yfiua/index-constituents` | Apache-2.0 | `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79` | Imported early on, then removed on 2026-10-08: fixed monthly snapshots are stale relative to the official current constituents. Not used by the current implementation. |
| `akfamily/akshare` | MIT | `akshare==1.19.1` | Pinned runtime dependency; no AkShare source is copied into the repository. |

## Candidate Comparison

| Project | License | Stars | Last Push | Role | Verdict |
|---|---:|---:|---|---|---|
| `shy3130/tick-stock-panel` | MIT | 5,293 | 2026-09-28 | Complete A-share workbench: FastAPI, React, Polars, DuckDB, factors, backtests, plugins, CI, 2,400+ tests | **Selected as application base** |
| `yfiua/index-constituents` | Apache-2.0 | 63 | 2026-09-01 | Monthly CSI300 constituent snapshots since 2023-07 | Removed; superseded by the official dynamic current-constituent endpoint |
| `akfamily/akshare` | MIT | 13,400 | 2026-10-05 | A-share market data SDK with raw daily bars, factors, financials | **Selected as data dependency** |
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
- Its plugin system allows adding AkShare without rewriting storage, services, or UI.
- Its existing screener/factor/backtest paths accept symbol pools; the HS300 filter intersects the pool with the official current constituents rather than creating a parallel evaluation framework.

## Why `index-constituents` was dropped (2026-10-08)

- The vendored snapshots stopped updating after 2026-01, while the official current constituents keep changing; the two lists already differed by 19 symbols in 2026-09, so the archive silently answered a different question than "HS300 today".
- The CSI official endpoint (`index_stock_cons_csindex`) returns the full current list in one call, so an archived fixed pool adds staleness without adding accuracy.
- Trade-off: the official endpoint has no public "constituents as of arbitrary date" query, so historical backtests filter with the current list and carry survivorship bias. That limitation is surfaced in the UI and code comments instead of being hidden behind stale snapshots.

## Why `akshare`

- It is a maintained MIT-licensed SDK rather than a scraping script.
- It exposes stock/index/ETF metadata, daily/minute bars, adjustment factors, financial statements, and share changes. The builtin provider requests unadjusted raw prices so the existing routed `adj_factor` + enriched pipeline remains the single forward-adjustment owner.
- It is easier to pin and test than copying a crawler from another application.

## Reuse Boundary

Copy now:

1. `tick-stock-panel` runtime, tests, CI, Docker, frontend, and backend.

Use as pinned dependencies:

1. `akshare==1.19.1`.

Defer:

1. Qlib integration.
2. `free-stockdb` local engine.
3. Hikyuu or vn.py.
4. Any GPL/AGPL or license-ambiguous project.
