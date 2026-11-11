"""Shared parameter-scan statistics, robustness gates, and Monte Carlo ranking."""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.factor_research.fingerprints import sha256_json
from app.factor_research.signal_scan import block_bootstrap_mean_ci

FDR_THRESHOLD = 0.10
PRIMARY_BLOCK_SIZE = 20


def stats_for_period(
    ic: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
    *,
    horizon: int = 5,
    include_distribution: bool = False,
) -> dict[str, Any]:
    period = ic.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    values = period["ic"].to_numpy()
    if values.size == 0:
        result: dict[str, Any] = {
            "n_dates": 0,
            "directional_ic": None,
            "nw_t": None,
            "p_value": None,
            "average_cross_section": None,
        }
        if include_distribution:
            result["yearly"] = []
            result["yearly_sign_consistency"] = None
        return result
    nw = stats_v2.newey_west_t(values, lag=horizon)
    result = {
        "n_dates": int(values.size),
        "directional_ic": round(float(values.mean()) * sign, 6),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": (
            round(float(stats_v2.normal_two_sided_p(nw[0])), 8)
            if nw is not None
            else None
        ),
        "average_cross_section": round(float(period["n"].mean()), 3),
    }
    if include_distribution:
        yearly = (
            period.with_columns(pl.col("date").dt.year().alias("year"))
            .group_by("year")
            .agg(pl.col("ic").mean().alias("ic_mean"), pl.len().alias("n_dates"))
            .sort("year")
        )
        yearly_rows = [
            {
                "year": int(row["year"]),
                "directional_ic": round(float(row["ic_mean"]) * sign, 6),
                "n_dates": int(row["n_dates"]),
            }
            for row in yearly.iter_rows(named=True)
        ]
        result["yearly"] = yearly_rows
        result["yearly_sign_consistency"] = (
            round(
                sum(row["directional_ic"] > 0 for row in yearly_rows) / len(yearly_rows),
                6,
            )
            if yearly_rows
            else None
        )
    return result


def bootstrap_ic(
    ic: pl.DataFrame,
    start: date,
    end: date,
    sign: int,
    *,
    block_size: int,
    n_bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    values = ic.filter((pl.col("date") >= start) & (pl.col("date") <= end))["ic"].to_numpy()
    return block_bootstrap_mean_ci(
        values * sign,
        block_size=block_size,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )


def bootstrap_seed(
    research_id: str,
    family_id: str,
    parameter: int,
    block_size: int,
    n_bootstrap: int,
) -> int:
    token = sha256_json({
        "research_id": research_id,
        "family_id": family_id,
        "parameter": parameter,
        "block_size": block_size,
        "n_bootstrap": n_bootstrap,
    }).removeprefix("sha256:")
    return int(token[:8], 16)


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) > 0.0


def _trial_direction_positive(trial: dict[str, Any]) -> bool:
    return all(
        _positive(trial[period]["directional_ic"])
        for period in ("full", "train", "test")
    )


def annotate_plateau(trials: list[dict[str, Any]]) -> None:
    ordered = sorted(trials, key=lambda item: item["parameter"])
    size = len(ordered)
    for index, trial in enumerate(ordered):
        if size < 3:
            neighbor_indices: list[int] = []
        elif index == 0:
            neighbor_indices = [1, 2]
        elif index == size - 1:
            neighbor_indices = [size - 3, size - 2]
        else:
            neighbor_indices = [index - 1, index + 1]
        neighbors = [ordered[position] for position in neighbor_indices]
        robust = (
            size >= 3
            and len(neighbor_indices) >= 2
            and all(_trial_direction_positive(neighbor) for neighbor in neighbors)
            and _trial_direction_positive(trial)
        )
        trial["neighbor_parameters"] = [neighbor["parameter"] for neighbor in neighbors]
        trial["robust_plateau"] = robust
        trial["plateau_status"] = (
            "robust_plateau"
            if robust
            else "single_point_unverified"
            if size < 3
            else "not_robust"
        )


def initial_eligibility(trial: dict[str, Any]) -> bool:
    neutral = trial["industry_size_neutral"]["full"]["directional_ic"]
    q_value = trial.get("q_value")
    ci_low = trial.get("bootstrap_full", {}).get("ci_low")
    return (
        bool(trial.get("robust_plateau"))
        and _trial_direction_positive(trial)
        and _positive(neutral)
        and isinstance(q_value, (int, float))
        and float(q_value) <= FDR_THRESHOLD
        and _positive(ci_low)
    )


def select_robust_candidates(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [trial for trial in trials if initial_eligibility(trial)],
        key=lambda item: item["parameter"],
    )


def final_ci_low(trial: dict[str, Any]) -> Any:
    final = trial.get("final_bootstrap", {})
    primary = final.get(str(PRIMARY_BLOCK_SIZE), final.get(PRIMARY_BLOCK_SIZE))
    return primary.get("ci_low") if isinstance(primary, dict) else None


def choose_recommended_candidate(
    trials: list[dict[str, Any]],
    *,
    current_parameter: int,
) -> dict[str, Any] | None:
    eligible = [
        trial
        for trial in trials
        if initial_eligibility(trial)
        and _positive(trial.get("train", {}).get("directional_ic"))
        and _positive(trial.get("test", {}).get("directional_ic"))
        and _positive(final_ci_low(trial))
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda trial: (
            min(
                float(trial["train"]["directional_ic"]),
                float(trial["test"]["directional_ic"]),
            ),
            float(final_ci_low(trial)),
            -abs(int(trial["parameter"]) - current_parameter),
        ),
    )


def assign_family_q_values(trials: list[dict[str, Any]]) -> None:
    raw_q = stats_v2.bh_fdr_qvalues([
        trial["full"]["p_value"] for trial in trials
    ])
    industry_q = stats_v2.bh_fdr_qvalues([
        trial["industry_neutral"]["full"]["p_value"] for trial in trials
    ])
    size_q = stats_v2.bh_fdr_qvalues([
        trial["size_neutral"]["full"]["p_value"] for trial in trials
    ])
    combined_q = stats_v2.bh_fdr_qvalues([
        trial["industry_size_neutral"]["full"]["p_value"] for trial in trials
    ])
    for trial, raw, industry, size, combined in zip(
        trials,
        raw_q,
        industry_q,
        size_q,
        combined_q,
        strict=True,
    ):
        trial["q_value"] = round(float(raw), 8) if raw is not None else None
        trial["industry_neutral"]["q_value"] = (
            round(float(industry), 8) if industry is not None else None
        )
        trial["size_neutral"]["q_value"] = (
            round(float(size), 8) if size is not None else None
        )
        trial["industry_size_neutral"]["q_value"] = (
            round(float(combined), 8) if combined is not None else None
        )


def initial_failure_counts(trials: list[dict[str, Any]]) -> dict[str, int]:
    checks = {
        "robust_plateau": lambda trial: bool(trial.get("robust_plateau")),
        "full_ic_positive": lambda trial: _positive(
            trial["full"]["directional_ic"]
        ),
        "train_ic_positive": lambda trial: _positive(
            trial["train"]["directional_ic"]
        ),
        "test_ic_positive": lambda trial: _positive(
            trial["test"]["directional_ic"]
        ),
        "industry_size_ic_positive": lambda trial: _positive(
            trial["industry_size_neutral"]["full"]["directional_ic"]
        ),
        "fdr_q_le_0_10": lambda trial: (
            isinstance(trial.get("q_value"), (int, float))
            and float(trial["q_value"]) <= FDR_THRESHOLD
        ),
        "initial_bootstrap_ci_positive": lambda trial: _positive(
            trial.get("bootstrap_full", {}).get("ci_low")
        ),
    }
    return {
        name: sum(not check(trial) for trial in trials)
        for name, check in checks.items()
    }
