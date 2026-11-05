from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r349_defensive_dual_momentum.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("r349_defensive_dual_momentum", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dual_momentum_signal_applies_only_to_following_returns() -> None:
    module = _load_module()
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(90)]
    gold = pl.DataFrame({"date": days, "close": [100.0 * 1.003**index for index in range(len(days))]})
    treasury = pl.DataFrame({"date": days, "close": [100.0 * 1.001**index for index in range(len(days))]})
    dividend = pl.DataFrame({"date": days, "close": [100.0 * 1.002**index for index in range(len(days))]})

    proxy = module.dual_momentum_proxy(gold, treasury, dividend, round_trip_cost=0.001)
    signal_day = date(2024, 3, 1)
    previous_day = date(2024, 2, 29)
    next_day = date(2024, 3, 2)

    assert proxy["weights_by_date"][previous_day] == {"gold": 1 / 3, "treasury": 1 / 3, "dividend": 1 / 3}
    assert proxy["weights_by_date"][signal_day] == {"gold": 0.5, "treasury": 0.0, "dividend": 0.5}

    month_rows = [row for row in proxy["schedule"] if row["date"] == signal_day]
    assert len(month_rows) == 1
    assert month_rows[0]["selected"] == ["gold", "dividend"]
    assert math.isclose(month_rows[0]["turnover"], 1.0 / 3.0, rel_tol=1e-12)

    curve = {row["date"]: row["close"] for row in proxy["curve"].iter_rows(named=True)}
    expected = curve[signal_day] * (1.0 + 0.5 * 0.003 + 0.5 * 0.002 - 0.001 / 3.0)
    assert math.isclose(curve[next_day], expected, rel_tol=1e-12)


def test_dual_momentum_turnover_never_exceeds_half() -> None:
    module = _load_module()
    days = [date(2023, 1, 2) + timedelta(days=index) for index in range(700)]
    gold = pl.DataFrame({"date": days, "close": [100.0 + index**1.05 for index in range(len(days))]})
    treasury = pl.DataFrame({"date": days, "close": [100.0 + index * 0.05 for index in range(len(days))]})
    dividend = pl.DataFrame({"date": days, "close": [100.0 + index**1.02 for index in range(len(days))]})

    proxy = module.dual_momentum_proxy(gold, treasury, dividend, round_trip_cost=0.001)

    assert all(row["turnover"] <= 0.5 + 1e-12 for row in proxy["schedule"])
    assert all(math.isclose(sum(row["weights"].values()), 1.0, rel_tol=1e-12) for row in proxy["schedule"])
