from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_alt_factor_data.py"
SPEC = importlib.util.spec_from_file_location("build_alt_factor_data", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
build_alt_factor_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_alt_factor_data)


def test_fund_holding_frame_respects_lag_and_computes_changes() -> None:
    raw = pl.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ"],
            "quarter_date": [date(2023, 3, 31), date(2023, 6, 30)],
            "fund_count": [10, 8],
            "holding_shares": [20.0, 10.0],
            "holding_value": [200.0, 120.0],
        }
    )
    instruments = pl.DataFrame(
        {"symbol": ["000001.SZ"], "float_shares": [100.0]}
    )
    dates = [
        date(2023, 5, 14),
        date(2023, 5, 15),
        date(2023, 8, 14),
        date(2023, 8, 15),
    ]

    out = build_alt_factor_data._fund_holding_frame(
        raw, dates, ["000001.SZ"], instruments
    ).sort("date")

    before = out.filter(pl.col("date") == date(2023, 5, 14)).row(0, named=True)
    q1 = out.filter(pl.col("date") == date(2023, 5, 15)).row(0, named=True)
    q2 = out.filter(pl.col("date") == date(2023, 8, 14)).row(0, named=True)

    assert before["fund_count_low"] is None
    assert q1["fund_count_low"] == -math.log1p(10)
    assert q1["fund_holding_ratio_low"] == -0.2
    assert q1["fund_count_chg_qoq_low"] is None
    assert q2["fund_count_low"] == -math.log1p(8)
    assert q2["fund_holding_ratio_low"] == -0.1
    assert q2["fund_count_chg_qoq_low"] == pytest.approx(0.2)
    assert q2["fund_holding_ratio_chg_qoq_low"] == pytest.approx(0.5)
    assert q2["fund_holding_value_chg_qoq_low"] == pytest.approx(0.4)


def test_decayed_event_score_starts_next_trading_day() -> None:
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    scores = build_alt_factor_data._decayed_event_score(
        [date(2024, 1, 2)],
        [2.0],
        [day.toordinal() for day in dates],
        window=2,
    )

    assert scores == [0.0, 2.0, 1.0]


def test_hk_risk_daily_ignores_extra_index_columns(tmp_path, monkeypatch) -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(25)]
    hk_root = tmp_path / "research" / "r195_hk"
    hk_root.mkdir(parents=True)
    for symbol in ("HSI", "HSCEI"):
        pl.DataFrame(
            {"date": dates, "close": [100.0 + index for index in range(len(dates))]}
        ).write_parquet(hk_root / f"{symbol}.parquet")
    index_root = tmp_path / "kline_index_daily"
    for index, day in enumerate(dates):
        partition = index_root / f"date={day.isoformat()}"
        partition.mkdir(parents=True)
        frame = pl.DataFrame(
            {
                "symbol": ["000300.SH"],
                "date": [day],
                "close": [1000.0 + index],
            }
        )
        if index % 2 == 0:
            frame = frame.with_columns(pl.lit(1_700_000_000_000).alias("quote_ts"))
        frame.write_parquet(partition / "part.parquet")

    monkeypatch.setattr(build_alt_factor_data, "DATA", tmp_path)
    out = build_alt_factor_data._hk_risk_daily(dates)

    assert out.height == len(dates)
    assert out["hsi_mom_20d"].null_count() == 20
    assert out["hsciei_mom_20d"].null_count() == 20
    assert out["hsi_rel_hs300_20d"].null_count() == 20
