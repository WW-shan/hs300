from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r521_industry_index_momentum_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r521_industry_index_momentum.py"


def _module():
    assert RUNNER.exists(), f"missing R521 runner: {RUNNER}"
    spec = importlib.util.spec_from_file_location("r521_industry", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _history(start: str, prices: list[float], *, amount: float = 100.0) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(prices))
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": prices,
            "收盘": prices,
            "成交额": [amount] * len(prices),
        }
    )


def test_r521_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R521"
    assert payload["signal"]["lookbacks"] == [20, 60, 120]
    assert payload["signal"]["scores"] == ["raw_momentum", "risk_adjusted_momentum"]
    assert payload["execution"]["main_cost_bps"] == 40
    assert payload["execution"]["stress_cost_bps"] == 60
    assert payload["execution"]["warning_cost_bps"] == 100
    assert payload["selection"]["no_test_selection"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r521_month_end_signal_is_last_completed_day() -> None:
    module = _module()
    dates = [
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
    ]
    assert module.is_month_end_signal(dates, 2) is True
    assert module.is_month_end_signal(dates, 3) is False
    assert module.is_month_end_signal(dates, 1) is False


def test_r521_selection_uses_only_train_and_validation() -> None:
    module = _module()
    rows = [
        {"name": "train_good_test_bad", "train": {"annual": 0.10}, "validation": {"annual": 0.08}, "test": {"annual": -0.50}},
        {"name": "train_bad_test_good", "train": {"annual": -0.10}, "validation": {"annual": -0.08}, "test": {"annual": 0.50}},
    ]
    selected = module.select_from_fit_windows(rows)
    assert selected["name"] == "train_good_test_bad"


def test_r521_portfolio_never_uses_leverage_and_charges_turnover() -> None:
    module = _module()
    weights = {"A": 0.5, "B": 0.5}
    assert module.max_gross_exposure(weights) <= 1.0
    cost = module.turnover_cost(
        previous={}, current=weights, cost_bps=20.0
    )
    assert cost == 0.002


def test_r521_rejects_out_of_window_price_rows() -> None:
    module = _module()
    frame = pd.DataFrame(
        {
            "code": ["A", "A"],
            "date": pd.to_datetime(["2023-12-29", "2024-01-02"]),
            "open": [10.0, 10.0],
            "close": [10.0, 11.0],
        }
    )
    clipped = module.clip_history(frame, date(2024, 1, 2), date(2024, 1, 2))
    assert len(clipped) == 1
    assert clipped.iloc[0]["date"].date() == date(2024, 1, 2)


def test_r521_outputs_when_present_are_research_only() -> None:
    output = E / "r521_industry_index_momentum_2026-11-23.json"
    result = E / "r521_industry_index_momentum_2026-11-23.md"
    if not output.exists() or not result.exists():
        return
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["production_enabled"] is False
    assert payload["grid_count"] == 24
    assert payload["universe_count"] == 28
    assert payload["selected"]["name"] == "risk_adjusted_momentum_60_1"
    assert payload["gate"]["pass"] is False
    assert payload["gate"]["checks"]["validation_annual_min"] is False
    assert payload["gate"]["checks"]["test_mdd_min"] is False


def test_r521_month_end_signal_executes_at_next_open() -> None:
    module = _module()
    dates = [
        date(2024, 1, 29),
        date(2024, 1, 30),
        date(2024, 1, 31),
        date(2024, 2, 1),
        date(2024, 2, 2),
        date(2024, 2, 5),
        date(2024, 2, 6),
        date(2024, 2, 7),
    ]
    prices = pd.DataFrame(
        {
            "code": ["A"] * len(dates),
            "date": pd.to_datetime(dates),
            "open": [10.0, 10.0, 20.0, 10.0, 10.0, 9.0, 8.0, 7.0],
            "close": [10.0, 10.0, 20.0, 10.0, 10.0, 9.0, 8.0, 7.0],
        }
    )
    result = module.simulate(
        prices,
        start=date(2024, 1, 29),
        end=date(2024, 2, 7),
        lookback=1,
        score="raw_momentum",
        top_n=1,
        cost_bps=0.0,
    )
    assert result["curve"][-1]["value"] < 1.0


def test_r521_simulation_omits_pre_entry_cash_days() -> None:
    module = _module()
    dates = pd.bdate_range("2024-01-01", periods=40).date
    prices = pd.DataFrame(
        {
            "code": ["A"] * len(dates),
            "date": pd.to_datetime(dates),
            "open": [10.0] * len(dates),
            "close": [10.0] * len(dates),
        }
    )
    result = module.simulate(
        prices,
        start=date(2024, 1, 1),
        end=date(2024, 2, 23),
        lookback=5,
        score="raw_momentum",
        top_n=1,
        cost_bps=0.0,
    )
    assert result["stats"]["days"] < len(dates) - 1


def test_r521_window_end_is_not_shared_across_windows() -> None:
    module = _module()
    windows = {
        "train_start": "2018-06-01",
        "train_end": "2022-05-05",
        "validation_start": "2022-05-06",
        "validation_end": "2025-05-05",
        "test_start": "2025-05-06",
        "test_end": "2026-11-05",
        "recent_start": "2025-10-14",
    }
    assert module.window_bounds(windows, "train") == (date(2018, 6, 1), date(2022, 5, 5))
    assert module.window_bounds(windows, "validation") == (
        date(2022, 5, 6),
        date(2025, 5, 5),
    )
    assert module.window_bounds(windows, "test") == (date(2025, 5, 6), date(2026, 11, 5))
    assert module.window_bounds(windows, "recent") == (date(2025, 10, 14), date(2026, 11, 5))


def test_r521_warmup_is_outside_reported_window_returns() -> None:
    module = _module()
    dates = pd.bdate_range("2023-10-02", periods=390).date
    rng = np.random.default_rng(521)
    closes = np.cumprod(1.0 + rng.normal(0.0002, 0.005, len(dates)))
    prices = pd.DataFrame(
        {
            "code": ["A"] * len(dates),
            "date": pd.to_datetime(dates),
            "open": closes,
            "close": closes,
        }
    )
    result = module.simulate(
        prices,
        start=date(2024, 1, 2),
        end=date(2025, 6, 30),
        lookback=60,
        score="raw_momentum",
        top_n=1,
        cost_bps=0.0,
    )
    assert result["stats"]["days"] > 200
    assert result["first_entry"] is not None


def test_r521_bootstrap_uses_each_daily_return_once() -> None:
    module = _module()
    returns = [0.01, -0.005, 0.002] * 20
    result = module.block_bootstrap_total(
        returns,
        block=5,
        paths=200,
        seed=521,
    )
    assert result["n"] == 60
    assert result["total_p05"] <= result["total_p50"] <= result["total_p95"]


def test_r521_simulate_returns_window_daily_returns_not_cumulative_curve() -> None:
    module = _module()
    dates = pd.bdate_range("2023-01-02", periods=300).date
    prices = []
    for code, drift in (("A", 0.001), ("B", -0.0005), ("C", 0.0)):
        values = 100.0 * np.cumprod(np.full(len(dates), 1.0 + drift))
        prices.append(
            pd.DataFrame(
                {
                    "code": code,
                    "date": pd.to_datetime(dates),
                    "open": values,
                    "close": values,
                }
            )
        )
    frame = pd.concat(prices, ignore_index=True)
    result = module.simulate(
        frame,
        start=date(2024, 1, 2),
        end=date(2024, 12, 31),
        lookback=20,
        score="raw_momentum",
        top_n=1,
        cost_bps=0.0,
    )
    returns = result["window_returns"]
    assert returns
    assert all(-0.5 < value < 0.5 for value in returns)
    assert abs(np.prod(1.0 + np.asarray(returns)) - 1.0 - result["stats"]["total"]) < 1e-6
