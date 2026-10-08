# Third-Party Notices

This repository records the upstream provenance and local reuse boundary for its application baseline.

## tick-stock-panel

- Upstream repository: <https://github.com/shy3130/tick-stock-panel>
- Commit: `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`
- License: MIT; see [`LICENSE`](LICENSE).
- Imported paths: the complete application baseline at the repository root, including `backend/`, `frontend/`, `tests`, CI workflows, Docker files, documentation, data-source plugin infrastructure, and supporting assets.
- Import exclusions: `.git/`, `.venv/`, `node_modules/`, `gui-test-screenshots/`, and `community-qr-code.jpg`.
- Local modifications: the project adds the `app.hs300` dynamic current-membership service (official CSI300 endpoint; no fixed pool and no snapshot archive), an AkShare builtin provider, `/api/hs300` endpoints, HS300 filtering in the screener/strategy/factor backtests, and HS300 frontend presets. `AGENTS.md`, `README.md`, and project-specific design/plan documents describe the upstream-derived boundary. Upstream K-line, indicator, factor, chart, storage, and backtest engines remain in use.

## akshare

- Upstream repository: <https://github.com/akfamily/akshare>
- Version: 1.19.1
- License: MIT.
- Reuse status: pinned runtime dependency (`akshare==1.19.1`) for stock/index/ETF daily bars, adjustment factors, financial statements, and share changes; the provider stores unadjusted raw prices and forward adjustment stays in the local `adj_factor` + enriched pipeline. AkShare source code is not vendored into this repository.
- Implementation status: the local `akshare` builtin provider (daily/minute/realtime/adj_factor/financial datasets) is implemented under `backend/app/plugins/akshare/`; it is not a copy of upstream source. The provider manifest/module is included in the PyInstaller desktop bundle; akshare SDK dependencies are collected by PyInstaller's static analysis.
