from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "research_factor_set_portfolio.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("factor_set_portfolio_research", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rolling_year_windows_cover_quarterly_starts_without_crossing_end() -> None:
    windows = _module()._rolling_year_windows()

    assert len(windows) == 18
    assert windows[0][0] == date(2021, 7, 1)
    assert windows[-1][0] == date(2025, 10, 14)
    for start, end in windows:
        expected_end = date(start.year + 1, start.month, start.day) - timedelta(days=1)
        assert end == expected_end
        assert end <= date(2026, 11, 5)


def test_summarize_runs_reports_positive_rate_and_worst_drawdown() -> None:
    records = [
        {
            "error": None,
            "stats": {
                "total_return": 0.10,
                "annual_return": 0.11,
                "sharpe": 0.8,
                "max_drawdown": -0.05,
            },
        },
        {
            "error": None,
            "stats": {
                "total_return": -0.05,
                "annual_return": -0.04,
                "sharpe": -0.2,
                "max_drawdown": -0.12,
            },
        },
    ]

    summary = _module()._summarize_runs(records)

    assert summary["n"] == 2
    assert summary["positive"] == 1
    assert summary["positive_rate"] == pytest.approx(0.5)
    assert summary["median_total_return"] == pytest.approx(0.025)
    assert summary["worst_max_drawdown"] == pytest.approx(-0.12)


def test_cost_parameters_keep_stamp_tax_at_statutory_rate() -> None:
    module = _module()

    assert module._cost_parameters("1x") == {
        "fees_pct": 0.0002,
        "commission_pct": 0.0002,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 5.0,
    }
    assert module._cost_parameters("3x")["stamp_tax_pct"] == 0.0005
    with pytest.raises(ValueError, match="unsupported cost scenario"):
        module._cost_parameters("5x")
