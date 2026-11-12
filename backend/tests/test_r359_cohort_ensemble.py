from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
    "run_r359_cohort_ensemble.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r359_cohort_ensemble", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_three_cohorts_hold_cash_until_start_and_keep_fixed_initial_capital_weights() -> None:
    module = _module()
    days = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(4)]
    curves = [
        pl.DataFrame({"date": days, "value": [100.0, 110.0, 110.0, 121.0]}),
        pl.DataFrame({"date": days[1:], "value": [200.0, 180.0, 180.0]}),
        pl.DataFrame({"date": days[2:], "value": [300.0, 300.0]}),
    ]

    combined = module.combine_cohort_curves(curves, [days[0], days[1], days[2]])

    assert combined["date"].to_list() == days
    assert combined["value"].to_list() == pytest.approx(
        [1.0, (1.1 + 1.0 + 1.0) / 3.0, (1.1 + 0.9 + 1.0) / 3.0, (1.21 + 0.9 + 1.0) / 3.0]
    )


def test_cohort_start_dates_use_trading_sessions_not_calendar_days() -> None:
    module = _module()
    sessions = [
        day
        for offset in range(130)
        if (day := date(2024, 1, 2) + timedelta(days=offset)).weekday() < 5
    ]

    starts = module.cohort_start_dates(sessions, sessions[0])

    assert starts == [sessions[0], sessions[20], sessions[40]]
    assert (starts[1] - starts[0]).days != 20


def test_cohort_curve_rejects_a_missing_trading_day_after_activation() -> None:
    module = _module()
    days = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(4)]
    curves = [
        pl.DataFrame({"date": days, "value": [1.0, 1.0, 1.0, 1.0]}),
        pl.DataFrame({"date": [days[1], days[3]], "value": [1.0, 1.0]}),
        pl.DataFrame({"date": days[2:], "value": [1.0, 1.0]}),
    ]

    with pytest.raises(ValueError, match="daily calendar mismatch"):
        module.combine_cohort_curves(curves, [days[0], days[1], days[2]])


def test_cohort_trade_pnl_keeps_the_actual_subaccount_amount() -> None:
    module = _module()
    trades = [{"symbol": "000001.SZ", "pnl_amount": 900.0}]

    annotated = module.annotate_cohort_trades(trades, cohort_id=2)

    assert annotated == [
        {
            "symbol": "000001.SZ",
            "pnl_amount": 900.0,
            "cohort_id": 2,
            "cohort_initial_weight": pytest.approx(1.0 / 3.0),
        }
    ]
