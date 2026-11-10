from __future__ import annotations

import warnings
from datetime import date

import numpy as np
import polars as pl
import pytest

from app.factor_research.signal_scan import (
    block_bootstrap_mean_ci,
    calculate_rank_ic,
    summarise_ic,
    time_series_rank_regression,
)


def test_rank_ic_detects_perfect_and_reversed_orders() -> None:
    frame = pl.DataFrame({
        "date": [date(2026, 1, 1)] * 6,
        "factor": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "forward_return": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
    })
    reversed_frame = frame.with_columns((-pl.col("factor")).alias("factor"))

    assert calculate_rank_ic(frame, "factor", "forward_return", minimum=4)["ic"][0] == pytest.approx(1.0)
    assert calculate_rank_ic(reversed_frame, "factor", "forward_return", minimum=4)["ic"][0] == pytest.approx(-1.0)


def test_rank_ic_skips_dates_below_width_or_constant_cross_section() -> None:
    frame = pl.DataFrame({
        "date": [date(2026, 1, 1)] * 3 + [date(2026, 1, 2)] * 4,
        "factor": [1.0, 2.0, 3.0, 2.0, 2.0, 2.0, 2.0],
        "forward_return": [1.0, 2.0, 3.0, 1.0, 2.0, 3.0, 4.0],
    })

    assert calculate_rank_ic(frame, "factor", "forward_return", minimum=4).is_empty()


def test_summary_uses_direction_and_reports_yearly_sign_consistency() -> None:
    ic = pl.DataFrame({
        "date": [
            date(2023, 6, 1), date(2024, 6, 1), date(2025, 11, 1), date(2026, 6, 1),
        ],
        "ic": [-0.1, -0.2, -0.15, -0.05],
    })

    summary = summarise_ic(ic, recent_start=date(2025, 10, 14), direction="low")

    assert summary["full"]["ic_mean"] == pytest.approx(-0.125)
    assert summary["full"]["directional_ic_mean"] == pytest.approx(0.125)
    assert summary["recent"]["ic_mean"] == pytest.approx(-0.1)
    assert summary["yearly_sign_consistency"] == 1.0
    assert summary["nw_lag"] == 5


def test_time_series_rank_regression_reports_hac_significance() -> None:
    rng = np.random.default_rng(7)
    x = np.arange(240, dtype=float)
    y = 0.8 * x + rng.normal(0.0, 12.0, size=x.size)
    frame = pl.DataFrame({"date": list(range(x.size)), "signal": x, "forward_return": y})

    result = time_series_rank_regression(
        frame, "signal", "forward_return", lag=5
    )

    assert result["beta"] is not None and result["beta"] > 0.8
    assert result["nw_t"] is not None and result["nw_t"] > 3.0
    assert result["n"] == 240


def test_time_series_rank_regression_fails_closed_on_constant_signal() -> None:
    frame = pl.DataFrame({
        "date": list(range(60)),
        "signal": [1.0] * 60,
        "forward_return": list(range(60)),
    })

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = time_series_rank_regression(frame, "signal", "forward_return", lag=5)

    assert result["beta"] is None
    assert result["nw_t"] is None


def test_block_bootstrap_mean_ci_preserves_positive_mean_and_is_deterministic() -> None:
    values = np.linspace(0.01, 0.03, 120)

    first = block_bootstrap_mean_ci(values, block_size=20, n_bootstrap=500, seed=11)
    second = block_bootstrap_mean_ci(values, block_size=20, n_bootstrap=500, seed=11)

    assert first == second
    assert first["mean"] == pytest.approx(0.02)
    assert first["ci_low"] > 0.0
    assert first["prob_positive"] == 1.0
