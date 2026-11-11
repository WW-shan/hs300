"""Pure primitives for incremental factor and factor-set research."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal

import numpy as np
import polars as pl

from app.backtest import stats_v2
from app.factor_research.signal_scan import calculate_rank_ic

Direction = Literal["high", "low"]


def _score_column(factor_column: str) -> str:
    return f"_score_{factor_column}"


def rank_score_frame(
    frame: pl.DataFrame,
    factor_column: str,
    direction: Direction,
    *,
    output_column: str | None = None,
) -> pl.DataFrame:
    """Add a daily cross-sectional rank z-score without mutating the input."""
    if direction not in {"high", "low"}:
        raise ValueError(f"unsupported direction: {direction}")
    missing = {factor_column, "date"} - set(frame.columns)
    if missing:
        raise ValueError(f"rank score frame missing columns: {sorted(missing)}")
    target = output_column or _score_column(factor_column)
    rank = pl.col(factor_column).rank(method="average").over("date")
    mean = rank.mean().over("date")
    std = rank.std().over("date")
    score = pl.when(std > 1e-12).then((rank - mean) / std).otherwise(None)
    if direction == "low":
        score = -score
    return frame.with_columns(score.cast(pl.Float64).alias(target))


def build_rank_composite(
    frame: pl.DataFrame,
    weights: Mapping[str, float],
    directions: Mapping[str, Direction],
    output_column: str,
    *,
    minimum_weight_coverage: float = 0.5,
) -> tuple[pl.DataFrame, dict[str, str]]:
    """Build a weighted composite from daily rank scores with explicit coverage."""
    if not weights:
        raise ValueError("composite requires at least one factor")
    if not 0.0 <= minimum_weight_coverage <= 1.0:
        raise ValueError("minimum_weight_coverage must be between 0 and 1")
    missing = set(weights) - set(frame.columns)
    if missing:
        raise ValueError(f"composite factors missing from frame: {sorted(missing)}")
    unknown_directions = set(weights) - set(directions)
    if unknown_directions:
        raise ValueError(f"composite directions missing: {sorted(unknown_directions)}")
    weight_sum = float(sum(float(weight) for weight in weights.values()))
    if weight_sum <= 0.0:
        raise ValueError("composite weights must sum to a positive value")

    scored = frame
    score_columns: dict[str, str] = {}
    for factor_column in weights:
        score_column = _score_column(factor_column)
        scored = rank_score_frame(
            scored,
            factor_column,
            directions[factor_column],
            output_column=score_column,
        )
        score_columns[factor_column] = score_column

    numerator: pl.Expr | float = 0.0
    denominator: pl.Expr | float = 0.0
    for factor_column, raw_weight in weights.items():
        weight = float(raw_weight)
        score = pl.col(score_columns[factor_column])
        numerator = numerator + score.fill_null(0.0) * weight
        denominator = denominator + pl.when(score.is_not_null()).then(weight).otherwise(0.0)
    coverage = denominator / weight_sum
    scored = scored.with_columns(
        pl.when(coverage >= minimum_weight_coverage)
        .then(numerator / denominator)
        .otherwise(None)
        .cast(pl.Float64)
        .alias(output_column)
    )
    return scored, score_columns


def pairwise_cross_sectional_correlation(
    frame: pl.DataFrame,
    score_columns: Mapping[str, str],
    *,
    minimum_symbols: int = 30,
) -> tuple[dict[str, dict[str, float | None]], dict[str, dict[str, Any]]]:
    """Summarize daily cross-sectional Pearson correlation of rank scores."""
    if minimum_symbols < 2:
        raise ValueError("minimum_symbols must be at least 2")
    missing = {"date"} | set(score_columns.values())
    absent = sorted(missing - set(frame.columns))
    if absent:
        raise ValueError(f"correlation frame missing columns: {absent}")

    names = list(score_columns)
    matrix: dict[str, dict[str, float | None]] = {name: {} for name in names}
    summaries: dict[str, dict[str, Any]] = {}
    for left in names:
        matrix[left][left] = 1.0
        for right in names:
            if right <= left:
                continue
            left_column = score_columns[left]
            right_column = score_columns[right]
            daily = (
                frame.select(["date", left_column, right_column])
                .filter(
                    pl.col(left_column).is_not_null()
                    & pl.col(left_column).is_finite()
                    & pl.col(right_column).is_not_null()
                    & pl.col(right_column).is_finite()
                )
                .group_by("date")
                .agg(
                    pl.len().alias("n"),
                    pl.corr(pl.col(left_column), pl.col(right_column)).alias("corr"),
                )
                .filter(
                    (pl.col("n") >= minimum_symbols)
                    & pl.col("corr").is_not_null()
                    & pl.col("corr").is_finite()
                )
                .sort("date")
            )
            values = daily["corr"].to_numpy() if not daily.is_empty() else np.array([])
            summary = {
                "n_dates": int(values.size),
                "mean_symbols": (
                    round(float(daily["n"].mean()), 3) if values.size else None
                ),
                "mean_signed": round(float(values.mean()), 6) if values.size else None,
                "mean_abs": round(float(np.abs(values).mean()), 6) if values.size else None,
                "max_abs": round(float(np.abs(values).max()), 6) if values.size else None,
                "min_signed": round(float(values.min()), 6) if values.size else None,
                "max_signed": round(float(values.max()), 6) if values.size else None,
            }
            summaries[f"{left}|{right}"] = summary
            signed = summary["mean_signed"]
            matrix[left][right] = signed
            matrix[right][left] = signed
    return matrix, summaries


def marginal_ic(
    frame: pl.DataFrame,
    base_score_column: str,
    candidate_score_column: str,
    return_column: str,
    *,
    recent_start: date,
    minimum_symbols: int = 30,
    horizon: int = 5,
) -> dict[str, Any]:
    """Compare base IC with an 80/20 base-plus-candidate composite."""
    required = {
        "date",
        "symbol",
        base_score_column,
        candidate_score_column,
        return_column,
    }
    absent = sorted(required - set(frame.columns))
    if absent:
        raise ValueError(f"marginal IC frame missing columns: {absent}")
    working = (
        frame.select(
            [
                "date",
                "symbol",
                base_score_column,
                candidate_score_column,
                return_column,
            ]
        )
        .filter(
            pl.col(base_score_column).is_not_null()
            & pl.col(base_score_column).is_finite()
            & pl.col(candidate_score_column).is_not_null()
            & pl.col(candidate_score_column).is_finite()
            & pl.col(return_column).is_not_null()
            & pl.col(return_column).is_finite()
        )
        .with_columns(
            (
                0.8 * pl.col(base_score_column)
                + 0.2 * pl.col(candidate_score_column)
            ).alias("_combined")
        )
    )
    base_ic = calculate_rank_ic(
        working,
        base_score_column,
        return_column,
        minimum=minimum_symbols,
    )
    combined_ic = calculate_rank_ic(
        working,
        "_combined",
        return_column,
        minimum=minimum_symbols,
    )
    paired = (
        base_ic.select(["date", pl.col("ic").alias("base_ic")])
        .join(
            combined_ic.select(["date", pl.col("ic").alias("combined_ic")]),
            on="date",
            how="inner",
        )
        .sort("date")
    )
    delta = (
        paired["combined_ic"].to_numpy() - paired["base_ic"].to_numpy()
        if paired.height
        else np.array([])
    )
    nw = stats_v2.newey_west_t(delta, lag=horizon) if delta.size >= 3 else None
    recent = paired.filter(pl.col("date") >= recent_start)
    recent_delta = (
        recent["combined_ic"].to_numpy() - recent["base_ic"].to_numpy()
        if recent.height
        else np.array([])
    )
    return {
        "n_dates": int(delta.size),
        "base_ic_mean": (
            round(float(paired["base_ic"].mean()), 6) if paired.height else None
        ),
        "combined_ic_mean": (
            round(float(paired["combined_ic"].mean()), 6) if paired.height else None
        ),
        "directional_delta_mean": (
            round(float(delta.mean()), 6) if delta.size else None
        ),
        "delta_nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "delta_p_value": (
            round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
            if nw is not None
            else None
        ),
        "recent_directional_delta_mean": (
            round(float(recent_delta.mean()), 6) if recent_delta.size else None
        ),
    }


def portfolio_gate(
    baseline: Mapping[str, Mapping[str, float | None]],
    candidate: Mapping[str, Mapping[str, float | None]],
    *,
    return_tolerance: float = 0.01,
    sharpe_tolerance: float = 0.05,
    drawdown_tolerance: float = 0.02,
) -> dict[str, Any]:
    """Apply non-extreme, explicit degradation tolerances to portfolio results."""
    if return_tolerance < 0 or sharpe_tolerance < 0 or drawdown_tolerance < 0:
        raise ValueError("portfolio tolerances must be nonnegative")

    def value(metrics: Mapping[str, Mapping[str, float | None]], group: str, key: str) -> float:
        raw = metrics.get(group, {}).get(key)
        if not isinstance(raw, (int, float)):
            raise ValueError(f"portfolio metrics missing {group}.{key}")
        return float(raw)

    full_base = value(baseline, "full_2x", "annual_return")
    recent_base = value(baseline, "recent_2x", "total_return")
    full_candidate = value(candidate, "full_2x", "annual_return")
    recent_candidate = value(candidate, "recent_2x", "total_return")
    checks = {
        "full_annual_within_tolerance": full_candidate >= full_base - return_tolerance,
        "recent_total_within_tolerance": recent_candidate >= recent_base - return_tolerance,
        "full_sharpe_within_tolerance": value(candidate, "full_2x", "sharpe")
        >= value(baseline, "full_2x", "sharpe") - sharpe_tolerance,
        "recent_sharpe_within_tolerance": value(candidate, "recent_2x", "sharpe")
        >= value(baseline, "recent_2x", "sharpe") - sharpe_tolerance,
        "full_drawdown_within_tolerance": value(candidate, "full_2x", "max_drawdown")
        >= value(baseline, "full_2x", "max_drawdown") - drawdown_tolerance,
        "recent_drawdown_within_tolerance": value(candidate, "recent_2x", "max_drawdown")
        >= value(baseline, "recent_2x", "max_drawdown") - drawdown_tolerance,
        "full_3x_annual_within_tolerance": value(candidate, "full_3x", "annual_return")
        >= value(baseline, "full_3x", "annual_return") - return_tolerance,
        "not_simultaneously_worse_full_and_recent": not (
            full_candidate < full_base and recent_candidate < recent_base
        ),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "deltas": {
            "full_2x_annual_return": round(full_candidate - full_base, 6),
            "recent_2x_total_return": round(recent_candidate - recent_base, 6),
        },
        "tolerances": {
            "return": return_tolerance,
            "sharpe": sharpe_tolerance,
            "drawdown": drawdown_tolerance,
        },
    }
