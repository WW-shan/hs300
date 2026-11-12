"""Layered research promotion gates for long-only strategy candidates."""
from __future__ import annotations

import json
import statistics
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_POLICY_PATH = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
    "gate_policy_v2.json"
)


def evaluate_gate_policy_v2(
    candidate: Mapping[str, Any],
    *,
    policy_path: str | Path = DEFAULT_POLICY_PATH,
) -> dict[str, Any]:
    """Evaluate one candidate against the layered public research gates.

    The input is intentionally provider-neutral: callers extract metrics from
    frozen evidence into the documented mapping and this function only applies
    deterministic thresholds.
    """
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    tiers = policy["tiers"]
    main = _mapping(candidate.get("main_2x"))
    control = candidate.get("control_2x")
    stress = _mapping(candidate.get("stress_3x"))
    extreme = candidate.get("extreme_5x")
    concentration = _mapping(candidate.get("concentration"))
    weights = _mapping(candidate.get("weights"))
    max_positions = int(candidate.get("max_positions") or 0)
    fresh = [float(value) for value in main.get("fresh_starts", []) if _is_number(value)]
    fresh_positive = sum(value > 0.0 for value in fresh)
    fresh_median = statistics.median(fresh) if fresh else None
    stress_warnings = _five_x_warnings(extreme, stress)

    core_thresholds = tiers["core_2x"]["thresholds"]
    stress_thresholds = tiers["stress_3x"]["thresholds"]
    concentration_thresholds = tiers["concentration"]["thresholds"]
    minimum_symbols = max(
        int(tiers["concentration"]["distinct_symbols_minimum"]),
        max_positions * int(tiers["concentration"]["distinct_symbols_minimum_per_position"]),
    )

    data_integrity = _mapping(candidate.get("data_integrity"))
    gates: dict[str, Any] = {
        "data_integrity": data_integrity.get("pass") is True,
        "no_leverage": (
            candidate.get("no_leverage") is True
            and _number(weights.get("max_weight_sum")) is not None
            and float(weights["max_weight_sum"]) <= 1.0
            and _number(weights.get("min_target_weight")) is not None
            and float(weights["min_target_weight"]) >= 0.0
        ),
        "core_2x": {
            "annual_return": _at_least(main.get("annual"), core_thresholds["annual_return_min"]),
            "sharpe": _at_least(main.get("sharpe"), core_thresholds["sharpe_min"]),
            "max_drawdown": _at_least(main.get("mdd"), core_thresholds["max_drawdown_min"]),
            "year_2022": _at_least(main.get("year_2022"), core_thresholds["year_2022_min"]),
            "year_2023": _at_least(main.get("year_2023"), core_thresholds["year_2023_min"]),
            "recent_return": _greater_than(main.get("recent"), 0.0),
            "minimum_fresh_starts": fresh_positive >= int(core_thresholds["fresh_starts_positive_min"]),
            "fresh_start_median": fresh_median is not None and fresh_median > 0.0,
        },
        "control_2x": _control_gates(control, main),
        "stress": {
            "cost_factor": _at_least(stress.get("cost_factor"), stress_thresholds["minimum_cost_factor"]),
            "annual_return": _at_least(stress.get("annual"), stress_thresholds["annual_return_min"]),
            "sharpe": _at_least(stress.get("sharpe"), stress_thresholds["sharpe_min"]),
            "max_drawdown": _at_least(stress.get("mdd"), stress_thresholds["max_drawdown_min"]),
            "total_return": _greater_than(stress.get("total_return"), 0.0),
            "recent_return": _at_least(stress.get("recent"), stress_thresholds["recent_return_min"]),
        },
        "concentration": _concentration_gates(
            concentration,
            concentration_thresholds,
            candidate.get("is_multi_asset") is True,
            minimum_symbols,
        ),
    }
    hard_gates_pass = _all_leaf_gates_pass(gates)
    return {
        "id": candidate.get("id"),
        "policy_version": policy["version"],
        "status": "qualified_for_paper_review" if hard_gates_pass else "rejected",
        "hard_gates_pass": hard_gates_pass,
        "gates": gates,
        "metrics": {
            "fresh_positive_count": fresh_positive,
            "fresh_count": len(fresh),
            "fresh_median": fresh_median,
            "minimum_distinct_symbols": minimum_symbols,
        },
        "diagnostics": {
            "5x_warnings": stress_warnings,
            "5x_hard_rejection": False,
            "net_concentration": {
                "top1_net_share": concentration.get("top1_net_share"),
                "top3_net_share": concentration.get("top3_net_share"),
                "top5_net_share": concentration.get("top5_net_share"),
                "net_to_gross": concentration.get("net_to_gross"),
            },
            "control": {
                "available": isinstance(control, Mapping),
                "recent_available": (
                    isinstance(control, Mapping)
                    and _number(control.get("recent")) is not None
                ),
            },
            "concentration": {
                "gross_shares_available": all(
                    _is_number(concentration.get(key))
                    for key in ("top1_gross_share", "top3_gross_share", "top5_gross_share")
                ),
                "legacy_2x_net_fallback": (
                    not all(
                        _is_number(concentration.get(key))
                        for key in ("top1_gross_share", "top3_gross_share", "top5_gross_share")
                    )
                    and concentration.get("legacy_2x_net_concentration_pass") is True
                ),
                "legacy_5x_net_not_used_as_hard_gate": True,
                "asset_contribution_available": (
                    isinstance(concentration.get("asset_contribution_shares"), Mapping)
                    and bool(concentration.get("asset_contribution_shares"))
                ),
            },
        },
    }


def _concentration_gates(
    concentration: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    is_multi_asset: bool,
    minimum_symbols: int,
) -> dict[str, bool]:
    gross_keys = ("top1_gross_share", "top3_gross_share", "top5_gross_share")
    gross_available = all(_is_number(concentration.get(key)) for key in gross_keys)
    if gross_available:
        gross_gates = {
            "top1_gross_share": _at_most(
                concentration.get("top1_gross_share"),
                thresholds["top1_gross_share_max"],
            ),
            "top3_gross_share": _at_most(
                concentration.get("top3_gross_share"),
                thresholds["top3_gross_share_max"],
            ),
            "top5_gross_share": _at_most(
                concentration.get("top5_gross_share"),
                thresholds["top5_gross_share_max"],
            ),
        }
    else:
        gross_gates = {key: True for key in gross_keys}
    legacy_2x_fallback = (
        not gross_available
        and concentration.get("legacy_2x_net_concentration_pass") is True
    )
    return {
        "distinct_symbols": _at_least(concentration.get("distinct_symbols"), minimum_symbols),
        **gross_gates,
        "top_industry_gross_share": _at_most(
            concentration.get("top_industry_gross_share"),
            thresholds["top_industry_gross_share_max"],
        ),
        "max_symbol_loss_to_gross": _at_most(
            concentration.get("max_symbol_loss_to_gross"),
            thresholds["max_symbol_loss_to_gross_max"],
        ),
        "asset_contribution": _asset_contribution_gates(
            is_multi_asset,
            concentration.get("asset_contribution_shares"),
            thresholds["single_asset_contribution_max"],
        ),
        "net_share_diagnostic_only": True,
        "concentration_evidence": gross_available or legacy_2x_fallback,
    }


def _control_gates(control: Any, main: Mapping[str, Any]) -> dict[str, bool]:
    if not isinstance(control, Mapping):
        return {
            "available": False,
            "annual_return": False,
            "sharpe": False,
            "recent_return": False,
        }
    return {
        "available": True,
        "annual_return": _at_least(
            _difference(main.get("annual"), control.get("annual")),
            -0.02,
        ),
        "sharpe": _at_least(
            _difference(main.get("sharpe"), control.get("sharpe")),
            -0.10,
        ),
        "recent_return": (
            True
            if _number(control.get("recent")) is None
            else _at_least(
                _difference(main.get("recent"), control.get("recent")),
                -0.03,
            )
        ),
    }


def _asset_contribution_gates(
    is_multi_asset: bool,
    shares: Any,
    maximum: float,
) -> bool:
    if not is_multi_asset:
        return True
    if not isinstance(shares, Mapping) or not shares:
        return True
    values = [value for value in shares.values() if _is_number(value)]
    return len(values) == len(shares) and all(float(value) <= maximum for value in values)


def _five_x_warnings(extreme: Any, stress: Mapping[str, Any]) -> list[str]:
    if not isinstance(extreme, Mapping):
        return []
    warnings: list[str] = []
    annual = _number(extreme.get("annual"))
    total_return = _number(extreme.get("total_return"))
    mdd = _number(extreme.get("mdd"))
    stress_mdd = _number(stress.get("mdd"))
    if annual is not None and annual < 0.0:
        warnings.append("negative_5x_annual")
    if total_return is not None and total_return <= 0.0:
        warnings.append("negative_5x_total_return")
    if mdd is not None and stress_mdd is not None and mdd < stress_mdd - 0.05:
        warnings.append("5x_drawdown_deterioration")
    net_to_gross = _number(extreme.get("net_to_gross"))
    if net_to_gross is not None and net_to_gross < 0.10:
        warnings.append("thin_5x_net_to_gross")
    return warnings


def _all_leaf_gates_pass(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        return all(_all_leaf_gates_pass(item) for item in value.values())
    return True


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    return float(value) if _is_number(value) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _difference(left: Any, right: Any) -> float | None:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return None
    return left_number - right_number


def _at_least(value: Any, threshold: Any) -> bool:
    number = _number(value)
    return number is not None and number >= float(threshold)


def _at_most(value: Any, threshold: Any) -> bool:
    number = _number(value)
    return number is not None and number <= float(threshold)


def _greater_than(value: Any, threshold: Any) -> bool:
    number = _number(value)
    return number is not None and number > float(threshold)
