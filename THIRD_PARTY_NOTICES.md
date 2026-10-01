# Third-Party Notices

This repository records the upstream provenance and local reuse boundary for its application baseline and CSI300 snapshot data.

## tick-stock-panel

- Upstream repository: <https://github.com/shy3130/tick-stock-panel>
- Commit: `3c0d351efe3f817ab187a7dbd1df3ba43b5d2d39`
- License: MIT; see [`LICENSE`](LICENSE).
- Imported paths: the complete application baseline at the repository root, including `backend/`, `frontend/`, `tests`, CI workflows, Docker files, documentation, data-source plugin infrastructure, and supporting assets.
- Import exclusions: `.git/`, `.venv/`, `node_modules/`, `gui-test-screenshots/`, and `community-qr-code.jpg`.
- Local modifications: the project adds the `app.hs300` PIT membership service, an AData builtin provider, `/api/hs300` endpoints, strategy/factor PIT backtest filtering, HS300 frontend presets, and snapshot-aware packaging. `AGENTS.md`, `README.md`, and project-specific design/plan documents describe the upstream-derived boundary. Upstream K-line, indicator, factor, chart, storage, and backtest engines remain in use.

## index-constituents

- Upstream repository: <https://github.com/yfiua/index-constituents>
- Commit: `5f0846e5ed4285895b4dd26dcc2d771a6ce68b79`
- License: Apache-2.0; see [`vendor/index-constituents/LICENSE`](vendor/index-constituents/LICENSE).
- Imported paths: `vendor/index-constituents/LICENSE`, `README.md`, `requirements.txt`, `get-constituents.py`, `get-constituents-historical.py`, `update-monthly.sh`, and only `docs/**/constituents-csi300.csv` snapshot files.
- Local modifications: none; the imported files remain unchanged from the pinned upstream commit. No other index datasets were imported.

## adata

- Upstream repository: <https://github.com/1nchaos/adata>
- Version: 2.9.5
- License: Apache-2.0.
- Reuse status: pinned runtime dependency (`adata==2.9.5`) for instruments and daily bars; the provider stores unadjusted raw prices and forward adjustment stays in the local `adj_factor` + enriched pipeline. AData source code is not vendored into this repository.
- Implementation status: the local `adata` builtin provider (raw daily bars, instruments) is implemented under `backend/app/plugins/adata/`; it is not a copy of upstream source. The provider manifest/module and required SDK data files are included in the PyInstaller desktop bundle.
