"""Research policy loading and defaults."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.factor_research.models import ResearchPolicy

_DEFAULT: dict[str, Any] = {
    "schema_version": 1,
    "policy_id": "default_v1",
    "sample_sufficiency": {
        "rank_min_valid_cross_sections_5y": 120,
        "rank_min_valid_cross_sections_recent": 40,
        "rank_min_non_null_symbols_per_date": 30,
        "industry_min_valid_groups_per_date": 5,
        "event_min_informative_symbols_per_date": 3,
        "event_min_valid_dates": 40,
        "market_min_valid_dates_5y": 120,
        "minimum_non_null_ratio": 0.10,
    },
    "staleness_trading_days": {
        "price": 10,
        "sentiment": 10,
        "flow": 10,
        "industry": 20,
        "macro": 45,
        "financial": 120,
        "unknown": 0,
    },
    "cost": {
        "baseline_multiplier": 2,
        "hard_stress_multiplier": 3,
        "diagnostic_5x": {
            "hard_rejection": False,
            "warnings": [
                "negative_5x_annual",
                "negative_5x_total_return",
                "drawdown_deterioration",
                "thin_net_to_gross",
            ],
        },
    },
    "leverage": {
        "required": "none",
        "max_weight_sum": 1.0,
        "min_target_weight": 0.0,
    },
    "multiple_testing": {
        "require_trial_family": True,
        "missing_family_verdict": "inconclusive",
    },
    "parameter_audit": {
        "required_for_active": True,
        "center_point_platform_required": True,
        "correlated_grid_units_are_not_independent": True,
    },
}


def load_policy(data_dir: Path | None = None, policy_id: str = "default_v1") -> ResearchPolicy:
    path = None if data_dir is None else Path(data_dir) / "factor_research" / "policies" / f"{policy_id}.json"
    if path is not None and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ResearchPolicy.model_validate(payload)
    if policy_id != "default_v1":
        raise FileNotFoundError(f"policy not found: {policy_id}")
    return ResearchPolicy.model_validate(_DEFAULT)
