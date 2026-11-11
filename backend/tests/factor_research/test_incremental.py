from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.factor_research.incremental import (
    build_rank_composite,
    marginal_ic,
    pairwise_cross_sectional_correlation,
    portfolio_gate,
    rank_score_frame,
)


def _panel(*, dates: int = 40, symbols: int = 40, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    start = date(2024, 1, 2)
    rows: list[dict[str, object]] = []
    for day in range(dates):
        market_signal = rng.normal()
        for symbol in range(symbols):
            base = market_signal + rng.normal(scale=0.3)
            independent = rng.normal()
            candidate = 0.9 * base + 0.1 * independent
            rows.append(
                {
                    "symbol": f"S{symbol:03d}",
                    "date": start + timedelta(days=day),
                    "base": base,
                    "candidate": candidate,
                    "independent": independent,
                    "forward_return_5d": 0.02 * independent + 0.001 * base,
                }
            )
    return pl.DataFrame(rows)


def test_rank_score_frame_applies_low_direction_without_mutating_input() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "factor": [1.0, 2.0, 3.0, 4.0],
        }
    )

    scored = rank_score_frame(frame, "factor", "low")

    assert scored["_score_factor"].to_list() == pytest.approx(
        [value * -1.0 for value in rank_score_frame(frame, "factor", "high")["_score_factor"].to_list()]
    )
    assert "factor" in frame.columns
    assert "_score_factor" not in frame.columns


def test_build_rank_composite_requires_half_of_declared_weight() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "one": [1.0, 2.0, 3.0, 4.0],
            "two": [4.0, 3.0, 2.0, 1.0],
            "three": [1.0, None, None, None],
        }
    )

    out, scores = build_rank_composite(
        frame,
        {"one": 1 / 3, "two": 1 / 3, "three": 1 / 3},
        {"one": "high", "two": "high", "three": "high"},
        "composite",
        minimum_weight_coverage=0.5,
    )

    assert set(scores) == {"one", "two", "three"}
    assert out["composite"].is_not_null().sum() == 4


def test_pairwise_correlation_reports_mean_and_max_absolute_by_pair() -> None:
    frame = rank_score_frame(
        rank_score_frame(_panel(), "base", "high"), "candidate", "high"
    ).with_columns(
        pl.col("independent").rank().over("date").alias("independent")
    )

    _, summaries = pairwise_cross_sectional_correlation(
        frame,
        {"base": "_score_base", "candidate": "_score_candidate", "others": "independent"},
        minimum_symbols=30,
    )

    pair = summaries["base|candidate"]
    assert pair["n_dates"] == 40
    assert pair["mean_abs"] > 0.9
    assert pair["max_abs"] <= 1.0
    assert summaries["base|others"]["mean_abs"] < 0.3


def test_marginal_ic_is_positive_for_a_predictive_candidate() -> None:
    frame = _panel(dates=160, symbols=50)
    scored, _scores = build_rank_composite(
        frame,
        {"base": 1.0},
        {"base": "high"},
        "base_composite",
    )
    scored = rank_score_frame(scored, "candidate", "high")

    result = marginal_ic(
        scored,
        "base_composite",
        "_score_candidate",
        "forward_return_5d",
        recent_start=date(2024, 4, 1),
        minimum_symbols=30,
    )

    assert result["directional_delta_mean"] > 0
    assert result["delta_p_value"] < 0.10
    assert result["recent_directional_delta_mean"] > 0


def test_portfolio_gate_rejects_simultaneous_full_and_recent_degradation() -> None:
    baseline = {
        "full_2x": {"annual_return": 0.12, "sharpe": 1.0, "max_drawdown": -0.12},
        "recent_2x": {"total_return": 0.08, "sharpe": 0.7, "max_drawdown": -0.10},
        "full_3x": {"annual_return": 0.10},
    }
    candidate = {
        "full_2x": {"annual_return": 0.105, "sharpe": 0.96, "max_drawdown": -0.13},
        "recent_2x": {"total_return": 0.065, "sharpe": 0.66, "max_drawdown": -0.11},
        "full_3x": {"annual_return": 0.085},
    }

    gate = portfolio_gate(baseline, candidate)

    assert gate["checks"]["not_simultaneously_worse_full_and_recent"] is False
    assert gate["pass"] is False


def test_portfolio_gate_accepts_small_within_tolerance_drawdown_change() -> None:
    baseline = {
        "full_2x": {"annual_return": 0.12, "sharpe": 1.0, "max_drawdown": -0.12},
        "recent_2x": {"total_return": 0.08, "sharpe": 0.7, "max_drawdown": -0.10},
        "full_3x": {"annual_return": 0.10},
    }
    candidate = {
        "full_2x": {"annual_return": 0.121, "sharpe": 1.01, "max_drawdown": -0.125},
        "recent_2x": {"total_return": 0.081, "sharpe": 0.71, "max_drawdown": -0.105},
        "full_3x": {"annual_return": 0.101},
    }

    gate = portfolio_gate(baseline, candidate)

    assert gate["pass"] is True
