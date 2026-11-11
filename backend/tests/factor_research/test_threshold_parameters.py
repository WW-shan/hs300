from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.factor_research.catalog import load_latest_catalog
from app.factor_research.parameters import load_parameter_registry
from app.factor_research.threshold_parameters import (
    ThresholdRegistry,
    load_threshold_registry,
    validate_threshold_registry,
)

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"


def _catalog():
    catalog = load_latest_catalog(DATA)
    assert catalog is not None
    return catalog


def _registry_payload() -> dict:
    return {
        "schema_version": 1,
        "families": [
            {
                "family_id": "dividend_abs",
                "label": "股息率绝对阈值",
                "kind": "threshold",
                "source_factor_id": "ext_alt_hs300_dividend_yield",
                "mode": "absolute",
                "direction": "high",
                "parameter_name": "threshold",
                "parameter_grid": [0.0, 0.01, 0.02],
                "current_parameter": 0.01,
                "neutralization": ["industry", "size"],
                "data_class": "financial",
                "minimum_history": 1,
                "formula_template": "dividend_yield >= {parameter}",
            },
            {
                "family_id": "repurchase_hold",
                "label": "回购事件后持有期",
                "kind": "event_holding",
                "source_factor_id": "ext_alt_hs300_repurchase_event_60d",
                "event_key": "repurchase",
                "direction": "high",
                "parameter_name": "holding_days",
                "parameter_grid": [5, 10, 20],
                "current_parameter": 20,
                "neutralization": ["industry", "size"],
                "data_class": "flow",
                "minimum_history": 61,
                "formula_template": "constant_holding(repurchase, {parameter} days)",
            },
        ],
    }


def test_p4_manifest_contains_pre_registered_threshold_and_holding_families() -> None:
    registry = load_threshold_registry(DATA)

    assert len(registry.families) == 23
    assert sum(family.kind == "threshold" for family in registry.families) == 18
    assert sum(family.kind == "event_holding" for family in registry.families) == 5
    assert {family.mode for family in registry.families if family.kind == "threshold"} == {
        "absolute",
        "quantile",
    }


def test_p4_manifest_passes_catalog_and_window_registry_validation() -> None:
    registry = load_threshold_registry(DATA)
    errors = validate_threshold_registry(
        registry,
        _catalog(),
        load_parameter_registry(DATA),
    )

    assert errors == []


def test_threshold_registry_rejects_duplicate_family_id() -> None:
    payload = _registry_payload()
    payload["families"].append(dict(payload["families"][0]))
    registry = ThresholdRegistry.model_validate(payload)

    errors = validate_threshold_registry(
        registry,
        _catalog(),
        load_parameter_registry(DATA),
    )

    assert "duplicate threshold family id: dividend_abs" in errors


def test_threshold_registry_rejects_duplicate_source_mode() -> None:
    payload = _registry_payload()
    duplicate = dict(payload["families"][0])
    duplicate["family_id"] = "dividend_abs_duplicate"
    payload["families"].append(duplicate)
    registry = ThresholdRegistry.model_validate(payload)

    errors = validate_threshold_registry(
        registry,
        _catalog(),
        load_parameter_registry(DATA),
    )

    assert (
        "duplicate threshold source/mode: "
        "ext_alt_hs300_dividend_yield/absolute"
    ) in errors


def test_quantile_grid_must_stay_inside_open_unit_interval() -> None:
    payload = _registry_payload()
    payload["families"][0]["mode"] = "quantile"
    payload["families"][0]["parameter_grid"] = [0.1, 0.5, 1.0]
    payload["families"][0]["current_parameter"] = 0.5

    with pytest.raises(ValidationError, match="quantile thresholds"):
        ThresholdRegistry.model_validate(payload)


def test_event_holding_grid_must_be_positive_integer_days() -> None:
    payload = _registry_payload()
    payload["families"][1]["parameter_grid"] = [0, 5, 10]
    payload["families"][1]["current_parameter"] = 5

    with pytest.raises(ValidationError, match="holding days"):
        ThresholdRegistry.model_validate(payload)
