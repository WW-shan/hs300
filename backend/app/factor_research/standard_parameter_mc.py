"""Standard train-select / validation-confirm parameter Monte Carlo policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from app.factor_research.standard_validation import (
    plot_parameter_artifacts,
)

DEFAULT_CONSTRAINTS = {
    "min_trades": 20,
    "max_mdd": -0.20,
    "require_validation_positive": True,
    "require_plateau": True,
    "require_cost_monotonic": True,
}

TRADING_DAYS_PER_YEAR = 252


def bootstrap_equity_curve(
    curve: list[dict[str, Any]],
    *,
    block_size: int = 20,
    bootstrap_paths: int = 2000,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap a portfolio's realized equity curve with moving blocks.

    The input curve is observed strategy output, not a fitted return summary.
    Circular blocks preserve adjacent-day dependence while keeping every
    sampled path exactly as long as the source window.
    """
    values = np.asarray([row.get("value") for row in curve], dtype=np.float64)
    if values.size < 2 or not np.isfinite(values).all() or np.any(values <= 0.0):
        raise ValueError("equity curve requires at least two positive finite values")
    returns = values[1:] / values[:-1] - 1.0
    observations = int(returns.size)
    block_size = min(int(block_size), observations)
    if block_size <= 0 or bootstrap_paths <= 0:
        raise ValueError("invalid bootstrap configuration")

    blocks_per_path = int(np.ceil(observations / block_size))
    rng = np.random.default_rng(seed)
    starts = rng.integers(
        0,
        observations,
        size=(int(bootstrap_paths), blocks_per_path),
    )
    indices = (starts[..., None] + np.arange(block_size)) % observations
    sampled = returns[indices].reshape(int(bootstrap_paths), -1)[:, :observations]
    path_values = np.cumprod(1.0 + sampled, axis=1)
    totals = path_values[:, -1] - 1.0

    path_peaks = np.maximum.accumulate(
        np.concatenate([np.ones((path_values.shape[0], 1)), path_values], axis=1),
        axis=1,
    )
    drawdowns = path_values / path_peaks[:, 1:] - 1.0
    max_drawdowns = drawdowns.min(axis=1)
    path_means = sampled.mean(axis=1)
    path_volatility = sampled.std(axis=1, ddof=0)
    sharpes = np.divide(
        path_means,
        path_volatility,
        out=np.zeros_like(path_means),
        where=path_volatility > 1e-12,
    ) * np.sqrt(TRADING_DAYS_PER_YEAR)
    years = observations / TRADING_DAYS_PER_YEAR

    def quantile(values: np.ndarray, probability: float) -> float:
        return round(float(np.quantile(values, probability)), 8)

    return {
        "method": "moving_block_bootstrap",
        "block_size": block_size,
        "bootstrap_paths": int(bootstrap_paths),
        "seed": int(seed),
        "observations": observations,
        "total_p05": quantile(totals, 0.05),
        "total_p50": quantile(totals, 0.50),
        "total_p95": quantile(totals, 0.95),
        "annualized_p05": round(float(np.quantile((1.0 + totals) ** (1.0 / years) - 1.0, 0.05)), 8),
        "annualized_p50": round(float(np.quantile((1.0 + totals) ** (1.0 / years) - 1.0, 0.50)), 8),
        "annualized_p95": round(float(np.quantile((1.0 + totals) ** (1.0 / years) - 1.0, 0.95)), 8),
        "sharpe_p50": quantile(sharpes, 0.50),
        "mdd_p05": quantile(max_drawdowns, 0.05),
        "mdd_p50": quantile(max_drawdowns, 0.50),
        "prob_positive": round(float(np.mean(totals > 0.0)), 6),
    }


def _metric(run: dict[str, Any], key: str) -> float | None:
    value = (run.get("stats") or run).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _trades(run: dict[str, Any]) -> int:
    value = run.get("trade_count", (run.get("stats") or run).get("n_trades", 0))
    return int(value or 0)


def _center_distance(point: dict[str, Any], center: dict[str, float] | None) -> float:
    if not center:
        return 0.0
    values = point.get("params") or {}
    total = 0.0
    for key, center_value in center.items():
        try:
            total += abs(float(values.get(key, center_value)) - float(center_value))
        except (TypeError, ValueError):
            total += 1.0
    return total


def _rejection_reasons(point: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    train = point.get("train") or {}
    validation = point.get("validation") or {}
    train_annual = _metric(train, "annual_return")
    train_mdd = _metric(train, "max_drawdown")
    validation_annual = _metric(validation, "annual_return")
    if train_annual is None or train_annual < float(constraints.get("min_train_annual", 0.0)):
        reasons.append("train_annual_min")
    if train_mdd is None or train_mdd < float(constraints["max_mdd"]):
        reasons.append("max_mdd")
    if _trades(train) < int(constraints["min_trades"]):
        reasons.append("min_trades")
    if constraints["require_validation_positive"] and (
        validation_annual is None or validation_annual <= 0.0
    ):
        reasons.append("validation_annual_positive")
    if constraints["require_plateau"] and point.get("plateau_ok") is not True:
        reasons.append("plateau_ok")
    if constraints["require_cost_monotonic"] and point.get("cost_monotonic") is not True:
        reasons.append("cost_monotonic")
    return reasons


def select_standard_parameter(
    points: list[dict[str, Any]],
    constraints: dict[str, Any] | None = None,
    *,
    bootstrap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a parameter using train and validation only.

    The recent window is retained for reporting and is never read by the
    ranking key.  This is the enforceable difference from an ad-hoc scan.
    """
    if bootstrap:
        if bootstrap.get("method") != "moving_block_bootstrap":
            raise ValueError("bootstrap must use moving_block_bootstrap")
        if not isinstance(bootstrap.get("windows"), dict) or not bootstrap["windows"]:
            raise ValueError("moving_block_bootstrap requires non-empty windows")
    policy = {**DEFAULT_CONSTRAINTS, **(constraints or {})}
    center = policy.get("center") if isinstance(policy.get("center"), dict) else None
    records: list[dict[str, Any]] = []
    for point in points:
        reasons = _rejection_reasons(point, policy)
        records.append({
            **point,
            "rejection_reasons": reasons,
            "eligible": not reasons,
            "recent_used_for_selection": False,
        })
    eligible = [row for row in records if row["eligible"]]
    if eligible:
        selected = max(
            eligible,
            key=lambda row: (
                min(
                    float(_metric(row.get("train") or {}, "annual_return") or 0.0),
                    float(_metric(row.get("validation") or {}, "annual_return") or 0.0),
                ),
                float(_metric(row.get("validation") or {}, "annual_return") or 0.0),
                float(_metric(row.get("validation") or {}, "sharpe") or 0.0),
                float(_metric(row.get("train") or {}, "max_drawdown") or -1.0),
                -_center_distance(row, center),
            ),
        )
        selected = {
            "parameter": selected.get("parameter"),
            "params": selected.get("params") or {},
            "train": selected.get("train"),
            "validation": selected.get("validation"),
            "recent": selected.get("recent"),
            "eligible": True,
        }
    else:
        selected = None
    return {
        "selection_policy": "train_select_validation_confirm",
        "recent_used_for_selection": False,
        "constraints": policy,
        "grid_size": len(points),
        "selected": selected,
        "eligible": [
            {"parameter": row.get("parameter"), "params": row.get("params") or {}}
            for row in eligible
        ],
        "rejected": [
            {
                "parameter": row.get("parameter"),
                "params": row.get("params") or {},
                "rejection_reasons": row["rejection_reasons"],
            }
            for row in records
            if not row["eligible"]
        ],
        "points": records,
        "bootstrap": dict(bootstrap or {}),
    }


def build_standard_parameter_artifacts(
    result: dict[str, Any],
    output_directory: Path,
    *,
    prefix: str,
) -> list[dict[str, str]]:
    points = [
        {
            "parameter": row.get("parameter"),
            "train_return": _metric(row.get("train") or {}, "annual_return") or 0.0,
            "validation_return": _metric(row.get("validation") or {}, "annual_return") or 0.0,
            "mdd": _metric(row.get("train") or {}, "max_drawdown") or 0.0,
        }
        for row in result.get("points") or []
    ]
    paths = plot_parameter_artifacts(
        points,
        result.get("bootstrap") or {},
        output_directory,
        prefix=prefix,
    )
    return [
        {
            "kind": kind,
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for kind, path in zip(
            ("parameter_paths", "parameter_pareto", "parameter_bootstrap"),
            paths,
            strict=True,
        )
    ]
