from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest


def _runner():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/research_r542_recent_failure_attribution.py"
    spec = importlib.util.spec_from_file_location("research_r542_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _curve(values: list[float], dates: list[str] | None = None) -> list[dict]:
    dates = dates or [f"2026-01-{index + 1:02d}" for index in range(len(values))]
    return [
        {"date": date, "value": value}
        for date, value in zip(dates, values, strict=True)
    ]


def test_quintile_spread_uses_direction_and_forward_returns() -> None:
    runner = _runner()
    rows = []
    for day in ("2026-01-02", "2026-01-05"):
        for value in range(10):
            rows.append({
                "date": day,
                "symbol": f"S{value}",
                "factor": float(value),
                "forward_return_5d": value * 0.01,
            })
    panel = pl.DataFrame(rows)

    high = runner.quintile_spread(
        panel,
        "factor",
        "forward_return_5d",
        direction="high",
        start="2026-01-02",
        end="2026-01-05",
    )
    low = runner.quintile_spread(
        panel,
        "factor",
        "forward_return_5d",
        direction="low",
        start="2026-01-02",
        end="2026-01-05",
    )

    assert high["n_dates"] == 2
    assert high["top_mean"] == pytest.approx(0.085)
    assert high["bottom_mean"] == pytest.approx(0.005)
    assert high["spread"] == pytest.approx(0.08)
    assert low["spread"] == pytest.approx(-0.08)


def test_loo_contribution_sign_convention() -> None:
    runner = _runner()
    assert runner.loo_contribution(0.10, 0.08) == pytest.approx(0.02)
    assert runner.loo_contribution(0.10, 0.13) == pytest.approx(-0.03)


def test_drawdown_episode_identifies_peak_trough_and_recovery() -> None:
    runner = _runner()
    episode = runner.drawdown_episode(
        _curve([100.0, 110.0, 99.0, 105.0, 120.0])
    )

    assert episode["peak_date"] == "2026-01-02"
    assert episode["trough_date"] == "2026-01-03"
    assert episode["recovery_date"] == "2026-01-05"
    assert episode["max_drawdown"] == pytest.approx(-0.10)


def test_portfolio_beta_regresses_strategy_on_benchmark() -> None:
    runner = _runner()
    benchmark = {
        "2026-01-02": 0.01,
        "2026-01-03": -0.02,
        "2026-01-04": 0.015,
        "2026-01-05": -0.005,
        "2026-01-06": 0.02,
    }
    values = [100.0]
    for day in sorted(benchmark):
        values.append(values[-1] * (1.0 + 1.5 * benchmark[day]))
    dates = ["2025-12-31", *sorted(benchmark)]
    beta = runner.portfolio_beta(_curve(values, dates), benchmark)

    assert beta["beta"] == pytest.approx(1.5)
    assert beta["n_days"] == 5


def test_regime_attribution_groups_strategy_and_benchmark_returns() -> None:
    runner = _runner()
    curve = _curve([100.0, 101.0, 99.99, 101.9898, 99.950004])
    benchmark = {
        "2026-01-02": 0.005,
        "2026-01-03": -0.005,
        "2026-01-04": 0.01,
        "2026-01-05": -0.01,
    }
    regime = pl.DataFrame({
        "date": sorted(benchmark),
        "state": ["weak", "weak", "strong", "strong"],
        "phase": ["ice", "ice", "up", "up"],
    })

    result = runner.regime_attribution(curve, benchmark, regime)
    by_state = {row["state"]: row for row in result["states"]}

    assert result["coverage"]["matched_days"] == 4
    assert by_state["weak"]["days"] == 2
    assert by_state["strong"]["days"] == 2
    assert by_state["weak"]["mean_excess_daily"] == pytest.approx(0.0)


def test_scientific_design_freezes_all_formal_runs() -> None:
    runner = _runner()
    design = runner.scientific_design()

    assert len(design["representatives"]) == 13
    assert design["single_leg_runs"] == 26
    assert design["leave_one_out_runs"] == 26
    assert design["baseline_runs"] == 2
    assert design["no_stop_control_runs"] == 2
    assert design["formal_runs"] == 56
    assert design["execution"]["matching"] == "open_t+1"
    assert design["execution"]["leverage"] == "none"
    assert design["execution"]["max_exposure_pct"] == 1.0
    assert design["output_policy"]["active_version"] is None


def test_period_return_excludes_start_close_return() -> None:
    runner = _runner()
    returns = {
        "2026-01-02": 0.10,
        "2026-01-05": 0.01,
        "2026-01-06": 0.02,
    }
    result = runner._period_return(
        returns,
        date(2026, 1, 2),
        date(2026, 1, 6),
    )
    assert result == pytest.approx(1.01 * 1.02 - 1.0)


def test_empty_benchmark_fails_closed() -> None:
    runner = _runner()
    with pytest.raises(ValueError, match="benchmark"):
        runner.portfolio_beta(_curve([100.0, 101.0]), {})
    with pytest.raises(ValueError, match="benchmark"):
        runner.regime_attribution(
            _curve([100.0, 101.0]),
            {},
            pl.DataFrame({"date": ["2026-01-02"], "state": ["weak"]}),
        )
