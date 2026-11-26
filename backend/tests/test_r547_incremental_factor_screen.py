from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r547_incremental_factor_screen.py"
CANDIDATES = ROOT / "data" / "factor_research" / "candidates" / "r547"
EXPECTED_IDS = {
    "cf_r547_cash_quality",
    "cf_r547_growth_quality",
    "cf_r547_industry_quality",
    "cf_r547_operating_efficiency",
    "cf_r547_smart_money_quality",
    "cf_r547_value_quality",
    "cf_r547_value_quality_safety",
}


def _runner():
    spec = importlib.util.spec_from_file_location("r547_incremental_factor_screen", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r547_declares_statistical_screen_contract() -> None:
    module = _runner()
    assert module.RESEARCH_TYPE == "statistical_screen"
    assert module.RESEARCH_ID == "R547"
    assert module.RESEARCH_PREREGISTRATION.endswith(
        "r547_incremental_factor_screen_preregistration.json"
    )


def test_r547_candidate_definitions_are_complete_and_sidecar_bound() -> None:
    module = _runner()
    definitions = module.load_candidate_definitions(ROOT)
    assert set(definitions) == EXPECTED_IDS
    for factor_id, definition in definitions.items():
        assert definition["id"] == factor_id
        assert definition["kind"] == "composite"
        assert definition["status"] == "draft"
        members = definition["members"]
        assert 3 <= len(members) <= 6
        assert abs(sum(abs(float(weight)) for weight in members.values()) - 1.0) <= 1e-9
        path = CANDIDATES / f"{factor_id}.json"
        sidecar = path.with_name(path.name + ".sha256")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r547_gate_uses_train_and_validation_but_only_reports_recent() -> None:
    module = _runner()
    passing = module.candidate_gate(
        candidate={
            "coverage": {"factor_non_null_ratio": 0.93, "valid_dates": 900},
            "train_ic": 0.018,
            "validation_ic": 0.006,
            "recent_ic": -0.001,
            "incremental": {
                "train": {"directional_delta": 0.004, "nw_p": 0.12},
            },
            "q_value": 0.15,
            "correlation_to_stable7": 0.44,
        }
    )
    assert passing["checks"]["recent_ic_positive"] is False
    assert passing["selection_pass"] is True
    assert passing["conclusion"] == "finalist"

    failing = module.candidate_gate(
        candidate={
            "coverage": {"factor_non_null_ratio": 0.93, "valid_dates": 900},
            "train_ic": 0.018,
            "validation_ic": -0.001,
            "recent_ic": 0.02,
            "incremental": {
                "train": {"directional_delta": 0.004, "nw_p": 0.12},
            },
            "q_value": 0.15,
            "correlation_to_stable7": 0.44,
        }
    )
    assert failing["checks"]["validation_ic_positive"] is False
    assert failing["selection_pass"] is False
    assert failing["conclusion"] == "rejected"


def test_r547_composite_materialization_preserves_availability() -> None:
    from datetime import date

    import polars as pl

    module = _runner()
    frame = pl.DataFrame(
        {
            "symbol": ["a", "b", "c", "d", "a", "b", "c", "d"],
            "date": [date(2024, 1, 2)] * 4 + [date(2024, 1, 3)] * 4,
            "positive": [1.0, 2.0, 3.0, 4.0, 1.0, 2.0, 3.0, 4.0],
            "negative": [4.0, 3.0, 2.0, None, 4.0, 3.0, 2.0, 1.0],
        }
    )
    definition = {
        "id": "cf_r547_test",
        "kind": "composite",
        "version": 1,
        "label": "test",
        "members": {"positive": 0.5, "negative": -0.5},
        "status": "draft",
    }
    result = module.materialize_composite(frame, definition, minimum_weight_coverage=1.0)
    assert result.filter(pl.col("date") == date(2024, 1, 2))["cf_r547_test"].null_count() == 1
    assert result.filter(pl.col("date") == date(2024, 1, 3))["cf_r547_test"].null_count() == 0
