from __future__ import annotations

import importlib.util
from datetime import date

import polars as pl


def _module():
    path = __import__("pathlib").Path(__file__).resolve().parents[2] / "scripts" / "research_r446_defensive_etf_rotation.py"
    spec = importlib.util.spec_from_file_location("r446_etf_timing", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monthly_switch_executes_at_next_open() -> None:
    module = _module()
    rows = []
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2)]
    prices = {
        "A": [10.0, 10.0, 10.0, 11.0, 12.0],
        "B": [10.0, 10.0, 10.0, 10.0, 9.0],
    }
    for symbol, values in prices.items():
        for day, value in zip(dates, values, strict=True):
            rows.append({"symbol": symbol, "date": day, "open": value, "close": value})
    frame = pl.DataFrame(rows)
    rule = {"lookback": 2, "mode": "relative_momentum"}
    curve = module.simulate(frame, rule, date(2024, 1, 2), date(2024, 2, 2), 0.0)
    assert curve[-1]["value"] > 1.0
    assert curve[0]["value"] == 1.0
