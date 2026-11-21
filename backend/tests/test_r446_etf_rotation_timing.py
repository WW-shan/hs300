from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import polars as pl


def _module(name: str = "research_r446_defensive_etf_rotation.py"):
    path = Path(__file__).resolve().parents[2] / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"etf_timing_{name[:20]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _switch_fixture() -> pl.DataFrame:
    # 2023-12-29 selects A/B. 2024-01-31 selects B/C because C has not yet
    # jumped. The switch is only executable at the 2024-02-01 open, so C's
    # prior 9 -> 15 move belongs to the old cash interval, not to the new book.
    rows = []
    prices = {
        "A": [10.0, 11.0, 11.0, 10.0, 10.0, 10.0],
        "B": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        "C": [10.0, 9.0, 9.0, 9.0, 15.0, 18.0],
    }
    dates = [
        date(2023, 12, 1),
        date(2023, 12, 29),
        date(2024, 1, 2),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 2),
    ]
    for symbol, values in prices.items():
        for day, value in zip(dates, values, strict=True):
            rows.append({"symbol": symbol, "date": day, "open": value, "close": value})
    return pl.DataFrame(rows)


def _assert_no_lookahead(module) -> None:
    curve = module.simulate(
        _switch_fixture(),
        {"lookback": 1, "mode": "relative_momentum"},
        date(2023, 12, 1),
        date(2024, 2, 2),
        0.0,
    )
    # Through 2024-01-31, A/B lose 11 -> 10 on half the book; then the new
    # B/C book earns only 15 -> 18 on C. The result is exactly 1.05.
    assert curve[-1]["value"] == 1.05


def test_monthly_switch_executes_at_next_open() -> None:
    _assert_no_lookahead(_module())


def test_corrected_research_module_has_same_execution_order() -> None:
    _assert_no_lookahead(_module("research_r447_defensive_etf_rotation_corrected.py"))
