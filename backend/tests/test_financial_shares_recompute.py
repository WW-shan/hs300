from __future__ import annotations

import polars as pl

from app.services import financial_sync


def test_share_sync_rebuilds_affected_enriched_and_refreshes_repo(
    tmp_path, monkeypatch
):
    scheduler = financial_sync.FinancialScheduler()
    scheduler._data_dir = tmp_path
    refreshed = []
    pipeline_calls = []

    class _Repo:
        def rebuild_views(self):
            refreshed.append("views")

        def refresh_cache(self, *, background=False):
            refreshed.append(("cache", background))

    scheduler._repo = _Repo()
    monkeypatch.setattr(financial_sync, "sync_shares", lambda *args: 2)
    monkeypatch.setattr(
        financial_sync,
        "get_financial_df",
        lambda data_dir, table: pl.DataFrame({
            "symbol": ["600519.SH", "600519.SH", "000300.SH"],
            "period_end": ["2020-06-30", "2026-06-30", "2026-06-30"],
        }),
    )
    monkeypatch.setattr(scheduler, "_record_sync", lambda table: None)

    def _run_pipeline(**kwargs):
        pipeline_calls.append(kwargs)
        return 3

    monkeypatch.setattr("app.indicators.pipeline.run_pipeline", _run_pipeline)
    monkeypatch.setattr(financial_sync, "_refresh_financials_views", lambda data_dir: None)

    result = scheduler._run_body("shares")

    assert result == {"shares": 2}
    assert pipeline_calls == [{
        "data_dir": tmp_path,
        "symbols": ["000300.SH", "600519.SH"],
    }]
    assert refreshed == ["views", ("cache", True)]
