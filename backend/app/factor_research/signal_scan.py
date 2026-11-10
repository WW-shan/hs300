"""Cross-sectional rank-IC research primitives."""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import polars as pl

from app.backtest import stats_v2


def calculate_rank_ic(
    frame: pl.DataFrame,
    factor_col: str,
    return_col: str,
    *,
    minimum: int = 30,
) -> pl.DataFrame:
    """Return one rank IC per date with a minimum cross-section width."""
    missing = {factor_col, return_col, "date"} - set(frame.columns)
    if missing:
        raise ValueError(f"rank IC frame missing columns: {sorted(missing)}")
    return (
        frame.filter(
            pl.col(factor_col).is_not_null()
            & pl.col(factor_col).is_finite()
            & pl.col(return_col).is_not_null()
            & pl.col(return_col).is_finite()
        )
        .group_by("date")
        .agg([
            pl.len().alias("n"),
            pl.corr(
                pl.col(factor_col).rank(method="average"),
                pl.col(return_col).rank(method="average"),
            ).alias("ic"),
        ])
        .filter((pl.col("n") >= minimum) & pl.col("ic").is_not_null() & pl.col("ic").is_finite())
        .sort("date")
    )


def _ic_stats(values: np.ndarray, direction_sign: int) -> dict[str, Any]:
    if values.size == 0:
        return {
            "n_dates": 0,
            "ic_mean": None,
            "ic_std": None,
            "ir": None,
            "win_rate": None,
            "directional_ic_mean": None,
        }
    mean = float(values.mean())
    std = float(values.std())
    return {
        "n_dates": int(values.size),
        "ic_mean": round(mean, 6),
        "ic_std": round(std, 6),
        "ir": round(mean / std, 6) if std > 1e-12 else None,
        "win_rate": round(float(np.mean(values > 0)), 6),
        "directional_ic_mean": round(mean * direction_sign, 6),
    }


def summarise_ic(
    ic_frame: pl.DataFrame,
    *,
    recent_start: date,
    direction: str,
    nw_lag: int = 5,
) -> dict[str, Any]:
    """Summarise raw and direction-adjusted IC, yearly sign stability and NW t."""
    if "date" not in ic_frame.columns or "ic" not in ic_frame.columns:
        raise ValueError("IC frame requires date and ic columns")
    clean = ic_frame.filter(pl.col("ic").is_not_null() & pl.col("ic").is_finite()).sort("date")
    direction_sign = -1 if direction == "low" else 1
    values = clean["ic"].to_numpy()
    recent = clean.filter(pl.col("date") >= recent_start)
    recent_values = recent["ic"].to_numpy()
    full_stats = _ic_stats(values, direction_sign)
    recent_stats = _ic_stats(recent_values, direction_sign)
    nw = stats_v2.newey_west_t(values, lag=nw_lag)
    p_value = stats_v2.normal_two_sided_p(nw[0]) if nw is not None else None

    yearly_rows: list[dict[str, Any]] = []
    if not clean.is_empty():
        yearly = (
            clean.with_columns(pl.col("date").dt.year().alias("year"))
            .group_by("year")
            .agg(pl.col("ic").mean().alias("ic_mean"), pl.len().alias("n_dates"))
            .sort("year")
        )
        yearly_rows = [
            {
                "year": int(row["year"]),
                "ic_mean": round(float(row["ic_mean"]), 6),
                "n_dates": int(row["n_dates"]),
            }
            for row in yearly.iter_rows(named=True)
        ]
    directional_mean = full_stats["directional_ic_mean"]
    if directional_mean is None or directional_mean == 0.0 or not yearly_rows:
        sign_consistency = None
    else:
        matching = sum(
            (float(row["ic_mean"]) * direction_sign > 0) == (float(directional_mean) > 0)
            for row in yearly_rows
        )
        sign_consistency = round(matching / len(yearly_rows), 6)

    return {
        "direction": direction,
        "full": full_stats,
        "recent": recent_stats,
        "yearly": yearly_rows,
        "yearly_sign_consistency": sign_consistency,
        "nw_lag": nw_lag,
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": round(float(p_value), 8) if p_value is not None else None,
    }


def time_series_rank_regression(
    frame: pl.DataFrame,
    signal_col: str,
    return_col: str,
    *,
    lag: int,
) -> dict[str, Any]:
    """Rank-correlation slope with a Newey-West HAC standard error."""
    missing = {signal_col, return_col} - set(frame.columns)
    if missing:
        raise ValueError(f"time-series frame missing columns: {sorted(missing)}")
    clean = frame.filter(
        pl.col(signal_col).is_not_null()
        & pl.col(signal_col).is_finite()
        & pl.col(return_col).is_not_null()
        & pl.col(return_col).is_finite()
    )
    n = clean.height
    if n <= lag + 3:
        return {"beta": None, "nw_t": None, "p_value": None, "n": n, "lag": lag}
    x = clean[signal_col].rank(method="average").to_numpy()
    y = clean[return_col].rank(method="average").to_numpy()
    x_std = float(x.std())
    y_std = float(y.std())
    if x_std <= 1e-12 or y_std <= 1e-12:
        return {"beta": None, "nw_t": None, "p_value": None, "n": n, "lag": lag}
    x = (x - x.mean()) / x_std
    y = (y - y.mean()) / y_std
    xx = float(np.dot(x, x))
    beta = float(np.dot(x, y) / xx) if xx > 0.0 else None
    if beta is None:
        return {"beta": None, "nw_t": None, "p_value": None, "n": n, "lag": lag}
    residual = y - beta * x
    scores = x * residual
    long_variance = float(np.dot(scores, scores))
    for offset in range(1, lag + 1):
        weight = 1.0 - offset / (lag + 1)
        long_variance += 2.0 * weight * float(np.dot(scores[offset:], scores[:-offset]))
    variance = max(long_variance, 0.0) / (xx * xx)
    if variance <= 0.0:
        return {"beta": round(beta, 6), "nw_t": None, "p_value": None, "n": n, "lag": lag}
    t_stat = beta / np.sqrt(variance)
    return {
        "beta": round(beta, 6),
        "nw_t": round(float(t_stat), 6),
        "p_value": round(float(stats_v2.normal_two_sided_p(t_stat)), 8),
        "n": n,
        "lag": lag,
    }


def block_bootstrap_mean_ci(
    values: Any,
    *,
    block_size: int,
    n_bootstrap: int = 1000,
    seed: int = 20261110,
    confidence: float = 0.95,
) -> dict[str, float | int]:
    """Deterministic moving-block bootstrap for an autocorrelated mean series."""
    array = np.asarray(
        [value for value in values if value is not None and np.isfinite(value)],
        dtype=float,
    )
    if array.size == 0:
        raise ValueError("bootstrap values are empty")
    if block_size <= 0 or n_bootstrap <= 0 or not 0.0 < confidence < 1.0:
        raise ValueError("invalid bootstrap configuration")
    block_size = min(int(block_size), int(array.size))
    blocks_per_sample = int(np.ceil(array.size / block_size))
    rng = np.random.default_rng(seed)
    starts = rng.integers(
        0,
        array.size - block_size + 1,
        size=(n_bootstrap, blocks_per_sample),
    )
    offsets = np.arange(block_size)
    samples = array[starts[..., None] + offsets]
    samples = samples.reshape(n_bootstrap, -1)[:, : array.size]
    means = samples.mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    return {
        "mean": round(float(array.mean()), 8),
        "ci_low": round(float(np.quantile(means, alpha)), 8),
        "ci_high": round(float(np.quantile(means, 1.0 - alpha)), 8),
        "prob_positive": round(float(np.mean(means > 0.0)), 6),
        "n": int(array.size),
        "block_size": block_size,
        "n_bootstrap": int(n_bootstrap),
    }
