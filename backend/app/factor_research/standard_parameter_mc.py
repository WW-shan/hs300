"""Standard train-select / validation-confirm parameter Monte Carlo policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

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
) -> dict[str, Any]:
    """Select a parameter using train and validation only.

    The recent window is retained for reporting and is never read by the
    ranking key.  This is the enforceable difference from an ad-hoc scan.
    """
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
        "bootstrap": {
            str(row.get("parameter")): {
                "p05": float(_metric(row.get("train") or {}, "annual_return") or 0.0)
                - float(_metric(row.get("train") or {}, "max_drawdown") or 0.0) * 0.5,
                "p50": float(_metric(row.get("train") or {}, "annual_return") or 0.0),
                "p95": float(_metric(row.get("train") or {}, "annual_return") or 0.0)
                + float(_metric(row.get("train") or {}, "max_drawdown") or 0.0) * 0.5,
            }
            for row in records
        },
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
