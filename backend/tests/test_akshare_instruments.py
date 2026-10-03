from __future__ import annotations

from datetime import date

import polars as pl

from app.services import instrument_sync


def test_sparse_akshare_instruments_preserve_existing_share_and_limit_fields(
    tmp_path, monkeypatch
):
    target = tmp_path / "instruments" / "instruments.parquet"
    target.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600519.SH"],
        "name": ["旧名称"],
        "listing_date": [date(2001, 8, 27)],
        "total_shares": [1256197800.0],
        "float_shares": [1256197800.0],
        "tick_size": [0.01],
        "limit_up": [110.0],
        "limit_down": [90.0],
    }).write_parquet(target)
    monkeypatch.setattr(
        instrument_sync,
        "_fetch_instruments_via_provider",
        lambda: [{
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "code": "600519",
            "exchange": "SH",
            "type": "stock",
            "listing_date": None,
            "total_shares": None,
            "float_shares": None,
            "tick_size": None,
            "limit_up": None,
            "limit_down": None,
        }],
    )

    assert instrument_sync.sync_instruments(tmp_path) == 1
    refreshed = pl.read_parquet(target)

    assert refreshed["name"].to_list() == ["贵州茅台"]
    assert refreshed["total_shares"].to_list() == [1256197800.0]
    assert refreshed["float_shares"].to_list() == [1256197800.0]
    assert refreshed["listing_date"].to_list() == [date(2001, 8, 27)]
    assert refreshed["tick_size"].to_list() == [0.01]
