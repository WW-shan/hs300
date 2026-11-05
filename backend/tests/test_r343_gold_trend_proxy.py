from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r343_gold_trend_proxy.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("r343_gold_trend_proxy", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_gold_trend_proxy_switches_only_after_month_start_signal() -> None:
    module = _load_module()
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(240)]
    gold = pl.DataFrame({"date": days, "close": [100.0] * 200 + [50.0] * 40})
    treasury = pl.DataFrame({"date": days, "close": [100.0 * 1.001**index for index in range(240)]})
    dividend = pl.DataFrame({"date": days, "close": [100.0 * 1.002**index for index in range(240)]})

    proxy = module.gold_trend_proxy(gold, treasury, dividend, round_trip_cost=0.001)

    august_1 = days.index(date(2024, 8, 1))
    assert math.isclose(proxy["close"][august_1], 50.0, rel_tol=0.0, abs_tol=1e-12)
    expected_next = 50.0 * (1.0 + 0.5 * (0.001 + 0.002) - 0.001 / 3.0)
    assert math.isclose(proxy["close"][august_1 + 1], expected_next, rel_tol=0.0, abs_tol=1e-10)


def test_gold_trend_proxy_is_positive_and_complete() -> None:
    module = _load_module()
    days = [date(2023, 1, 1) + timedelta(days=index) for index in range(300)]
    gold = pl.DataFrame({"date": days, "close": [100.0 + index * 0.1 for index in range(300)]})
    treasury = pl.DataFrame({"date": days, "close": [100.0 * 1.0001**index for index in range(300)]})
    dividend = pl.DataFrame({"date": days, "close": [100.0 * 1.0002**index for index in range(300)]})

    proxy = module.gold_trend_proxy(gold, treasury, dividend, round_trip_cost=0.001)

    assert proxy.height == len(days)
    assert proxy["close"].is_finite().all()
    assert proxy["close"].min() > 0
