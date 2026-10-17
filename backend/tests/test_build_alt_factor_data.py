from __future__ import annotations

import importlib.util
import math
from datetime import date
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
