from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.factor_research.models import CatalogSnapshot, FactorRecord, SourceLocator
from app.factor_research.parameters import (
    ParameterFamilyRegistry,
    build_classification_payload,
    classify_factor_parameters,
    load_parameter_registry,
    validate_parameter_registry,
)


def _factor(factor_id: str) -> FactorRecord:
    return FactorRecord(
        factor_id=factor_id,
        factor_version=1,
        label=factor_id,
        group="test",
        kind="base",
        formula_text=factor_id,
        source=SourceLocator(type="builtin", module="test"),
        definition_fingerprint=f"fingerprint-{factor_id}",
    )


def _registry_payload(
    *,
    family_id: str = "idio_vol",
    member_factor_ids: list[str] | None = None,
    parameter_grid: list[int] | None = None,
    current_parameter: int = 20,
    status: str = "tunable",
) -> dict:
    return {
        "schema_version": 1,
        "families": [
            {
                "family_id": family_id,
                "label": "Residual volatility",
                "data_class": "price",
                "cross_section_type": "stock_cross_sectional",
                "direction": "high",
                "evaluator": "residual_volatility",
                "parameter_name": "window",
                "parameter_grid": parameter_grid or [20, 40, 60],
                "current_parameter": current_parameter,
                "minimum_history": 240,
                "neutralization": ["industry", "size"],
                "member_factor_ids": member_factor_ids or ["idio_vol_20"],
                "status": status,
                "evidence_refs": [],
            }
        ],
        "unclassified_factor_ids": [],
        "blocked_factor_ids": [],
    }


def test_registry_rejects_duplicate_family_member_bad_grid_and_missing_factor() -> None:
    payload = _registry_payload(member_factor_ids=["idio_vol_20"], parameter_grid=[60, 20])
    payload["families"].append(
        {
            **payload["families"][0],
            "family_id": "idio_vol",
            "member_factor_ids": ["idio_vol_20", "missing_factor"],
        }
    )
    registry = ParameterFamilyRegistry.model_validate(payload)

    errors = validate_parameter_registry(registry, {"idio_vol_20"})

    assert any("duplicate family" in error for error in errors)
    assert any("duplicate member" in error for error in errors)
    assert any("ascending" in error for error in errors)
    assert any("missing factor" in error for error in errors)


def test_registry_rejects_current_parameter_outside_grid_and_nonpositive_grid() -> None:
    registry = ParameterFamilyRegistry.model_validate(
        _registry_payload(parameter_grid=[20, 60], current_parameter=40)
    )

    errors = validate_parameter_registry(registry, {"idio_vol_20"})
    assert any("current parameter" in error for error in errors)

    with pytest.raises(ValidationError):
        ParameterFamilyRegistry.model_validate(
            _registry_payload(parameter_grid=[0, 20], current_parameter=20)
        )


def test_classification_marks_members_grouped_unregistered_windows_unclassified_and_fixed() -> None:
    factors = [
        _factor("idio_vol_20"),
        _factor("momentum_999d"),
        _factor("ma5_bias"),
        _factor("roe_latest"),
    ]
    registry = ParameterFamilyRegistry.model_validate(_registry_payload())

    rows = classify_factor_parameters(factors, registry)
    by_id = {row.factor_id: row for row in rows}

    assert by_id["idio_vol_20"].status == "grouped"
    assert by_id["idio_vol_20"].family_id == "idio_vol"
    assert by_id["momentum_999d"].status == "unclassified"
    assert by_id["ma5_bias"].status == "unclassified"
    assert by_id["roe_latest"].status == "fixed"
    assert [row.factor_id for row in rows] == sorted(by_id)


def test_blocked_family_members_fail_closed() -> None:
    factors = [_factor("idio_vol_20")]
    registry = ParameterFamilyRegistry.model_validate(
        _registry_payload(status="blocked")
    )

    row = classify_factor_parameters(factors, registry)[0]

    assert row.status == "blocked"
    assert row.family_id == "idio_vol"


def test_load_parameter_registry_uses_factor_research_layout(tmp_path: Path) -> None:
    path = tmp_path / "factor_research" / "parameters" / "families.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        """{
  "schema_version": 1,
  "families": [{
    "family_id": "idio_vol",
    "label": "Residual volatility",
    "data_class": "price",
    "cross_section_type": "stock_cross_sectional",
    "direction": "high",
    "evaluator": "residual_volatility",
    "parameter_name": "window",
    "parameter_grid": [20, 40, 60],
    "current_parameter": 20,
    "minimum_history": 240,
    "neutralization": ["industry", "size"],
    "member_factor_ids": ["idio_vol_20"],
    "status": "tunable",
    "evidence_refs": []
  }],
  "unclassified_factor_ids": [],
  "blocked_factor_ids": []
}
""",
        encoding="utf-8",
    )

    registry = load_parameter_registry(tmp_path)

    assert registry.families[0].family_id == "idio_vol"


def test_classification_payload_is_deterministic_and_counts_every_factor() -> None:
    factors = (_factor("idio_vol_20"), _factor("momentum_999d"), _factor("roe_latest"))
    factor_rows = list(factors)
    catalog = CatalogSnapshot(
        snapshot_id="sha256:catalog",
        generated_at="2026-11-11T00:00:00Z",
        registry_fingerprint="sha256:registry",
        factor_count=len(factor_rows),
        factors=factor_rows,
    )
    registry = ParameterFamilyRegistry.model_validate(_registry_payload())

    payload = build_classification_payload(
        catalog,
        registry,
        generated_at="2026-11-11T01:00:00Z",
    )
    repeat = build_classification_payload(
        catalog,
        registry,
        generated_at="2026-11-11T02:00:00Z",
    )

    assert payload["report_id"] == repeat["report_id"]
    assert payload["counts"] == {
        "total": 3,
        "tunable": 0,
        "fixed": 1,
        "grouped": 1,
        "blocked": 0,
        "unclassified": 1,
    }
    assert payload["catalog"]["snapshot_id"] == "sha256:catalog"
    assert len(payload["factors"]) == 3


def test_p1_manifest_covers_eight_families_and_all_catalog_members() -> None:
    from app.factor_research.catalog import load_latest_catalog

    root = Path(__file__).resolve().parents[3]
    catalog = load_latest_catalog(root / "data")
    assert catalog is not None
    registry = load_parameter_registry(root / "data")

    assert catalog.factor_count == 223
    assert len(catalog.factors) == 223
    assert {family.family_id for family in registry.families} == {
        "idio_vol",
        "cs_spread",
        "vol_of_vol",
        "market_down_beta",
        "price_delay",
        "trend_efficiency",
        "risk_adjusted_momentum",
        "beta_asymmetry",
    }
    assert validate_parameter_registry(registry, {item.factor_id for item in catalog.factors}) == []

    rows = classify_factor_parameters(catalog.factors, registry)
    assert len(rows) == 223
    assert all(
        row.status == "grouped"
        for row in rows
        if row.factor_id in {
            factor_id
            for family in registry.families
            for factor_id in family.member_factor_ids
        }
    )
