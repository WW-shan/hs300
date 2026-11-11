from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factor_research.automation import (
    assert_portfolio_candidates_admitted,
    automation_research_id,
    build_automation_payload,
    evaluate_factor_readiness,
)
from app.factor_research.models import (
    CatalogSnapshot,
    FactorRecord,
    SourceLocator,
)
from app.factor_research.parameters import (
    FactorParameterClassification,
    ParameterFamily,
    ParameterFamilyRegistry,
)
from app.factor_research.threshold_parameters import ThresholdRegistry


def _factor(
    factor_id: str,
    *,
    source_type: str = "builtin",
    data_class: str = "price",
    pit_status: str = "not_applicable",
    future_data_status: str = "clear",
) -> FactorRecord:
    return FactorRecord(
        factor_id=factor_id,
        factor_version=1,
        label=factor_id,
        group="test",
        kind="base",
        formula_text="close",
        direction="high",
        pit_status=pit_status,
        future_data_status=future_data_status,
        source=SourceLocator(type=source_type),
        data_class=data_class,
        cross_section_type="stock_cross_sectional",
        definition_fingerprint=f"sha256:{factor_id:0>64}"[-71:],
    )


def _catalog(*factors: FactorRecord) -> CatalogSnapshot:
    return CatalogSnapshot(
        snapshot_id="sha256:" + "1" * 64,
        generated_at="2026-11-11T00:00:00Z",
        registry_fingerprint="sha256:" + "2" * 64,
        factor_count=len(factors),
        factors=list(factors),
    )


def _classification(
    factor_id: str,
    status: str = "grouped",
) -> FactorParameterClassification:
    return FactorParameterClassification(
        factor_id=factor_id,
        factor_version=1,
        status=status,
        family_id="family" if status == "grouped" else None,
        parameter=20 if status == "grouped" else None,
        reason=status,
    )


def _audit(*, integrity: str = "pass", config_sha256: str = "sha256:" + "a" * 64):
    return {
        "manifest": {
            "input_fingerprints": {
                "ext_data": {"config_sha256": config_sha256},
            }
        },
        "findings": {
            "ext_factor": {
                "integrity": integrity,
                "signal": "unassessed",
                "incremental": "unknown",
                "investability": "not_tested",
                "overall": "watch_only",
                "reasons": [],
            }
        },
    }


def test_unclassified_factor_is_blocked_for_scan_and_portfolio() -> None:
    readiness = evaluate_factor_readiness(
        _catalog(_factor("factor_x")),
        [_classification("factor_x", "unclassified")],
        None,
        current_audit_config_sha256=None,
    )["factor_x"]

    assert readiness["scan_ready"] is False
    assert readiness["portfolio_ready"] is False
    assert "classification_unclassified" in readiness["reasons"]


def test_financial_declared_pit_can_research_but_cannot_enter_portfolio() -> None:
    readiness = evaluate_factor_readiness(
        _catalog(_factor("financial_x", data_class="financial", pit_status="declared")),
        [_classification("financial_x")],
        None,
        current_audit_config_sha256=None,
    )["financial_x"]

    assert readiness["scan_ready"] is True
    assert readiness["portfolio_ready"] is False
    assert "financial_pit_not_verified" in readiness["reasons"]


def test_stale_extension_audit_blocks_both_gates() -> None:
    readiness = evaluate_factor_readiness(
        _catalog(_factor("ext_factor", source_type="extension", pit_status="declared")),
        [_classification("ext_factor")],
        _audit(config_sha256="sha256:" + "b" * 64),
        current_audit_config_sha256="sha256:" + "a" * 64,
    )["ext_factor"]

    assert readiness["scan_ready"] is False
    assert readiness["portfolio_ready"] is False
    assert readiness["audit_status"] == "audit_stale"
    assert "audit_stale" in readiness["reasons"]


def test_custom_source_requires_explicit_future_admission() -> None:
    readiness = evaluate_factor_readiness(
        _catalog(_factor("uf_custom", source_type="custom")),
        [_classification("uf_custom")],
        None,
        current_audit_config_sha256=None,
    )["uf_custom"]

    assert readiness["scan_ready"] is False
    assert "source_not_automatable" in readiness["reasons"]


def test_portfolio_guard_fails_closed_when_latest_manifest_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="automation latest manifest"):
        assert_portfolio_candidates_admitted(tmp_path, ["factor_x"])


def test_portfolio_guard_rejects_blocked_factor(tmp_path: Path) -> None:
    automation = tmp_path / "factor_research" / "automation"
    runs = automation / "runs"
    runs.mkdir(parents=True)
    run_path = runs / "run.json"
    payload = {
        "research_id": "sha256:" + "3" * 64,
        "generated_at": "2026-11-11T00:00:00Z",
        "admission": {
            "factor_x": {
                "portfolio_ready": False,
                "reasons": ["classification_unclassified"],
            }
        },
    }
    payload["research_id"] = automation_research_id(payload)
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    (automation / "latest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "research_id": payload["research_id"],
                "run_json": "factor_research/automation/runs/run.json",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="classification_unclassified"):
        assert_portfolio_candidates_admitted(tmp_path, ["factor_x"])


def _window_family(*, family_id: str = "family_a") -> ParameterFamily:
    return ParameterFamily(
        family_id=family_id,
        label=family_id,
        direction="high",
        evaluator="test_evaluator",
        parameter_grid=[20, 40],
        current_parameter=20,
        member_factor_ids=["factor_a_20d"],
    )


def _payload(
    *,
    factor_ids: list[str] | None = None,
    previous: bool = False,
    recommended: bool = False,
) -> dict:
    families = {
        "families": {
            "family_a": {
                "recommendation_status": "recommended" if recommended else "not_recommended",
                "recommended_parameter": 20,
                "recommended_variant_id": "family_a__p_20",
            }
        }
    } if recommended else {"families": {}}
    payload = {
        "research_id": "sha256:" + ("4" if previous else "5") * 64,
        "families": families["families"],
    }
    if factor_ids is not None:
        payload["catalog"] = {"factor_ids": factor_ids}
    return payload


def _build(
    *,
    factor_ids: list[str],
    registry: ParameterFamilyRegistry,
    p1: dict,
    p5_candidates: list[dict] | None = None,
    previous: dict | None = None,
) -> dict:
    return build_automation_payload(
        catalog=_catalog(*(_factor(factor_id) for factor_id in factor_ids)),
        window_registry=registry,
        threshold_registry=ThresholdRegistry(),
        window_payloads={"p1": p1, "p2": {"research_id": "sha256:" + "6" * 64, "families": {}}},
        threshold_payload={"research_id": "sha256:" + "7" * 64, "families": {}},
        incremental_payload={
            "research_id": "sha256:" + "8" * 64,
            "p1_research_id": p1["research_id"],
            "p2_research_id": "sha256:" + "6" * 64,
            "p3_research_id": "sha256:" + "9" * 64,
            "p4_research_id": "sha256:" + "7" * 64,
            "candidates": p5_candidates or [],
        },
        ledger_bindings=[],
        previous_payload=previous,
        current_audit_config_sha256=None,
        audit=None,
        generated_at="2026-11-11T00:00:00Z",
    )


def test_unresearched_family_generates_parameter_scan_task() -> None:
    registry = ParameterFamilyRegistry(families=[_window_family()])
    payload = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=_payload(),
    )

    assert payload["pending_tasks"] == [
        {
            "task_id": "parameter_scan:window:family_a",
            "kind": "parameter_scan",
            "status": "pending",
            "family_id": "family_a",
            "family_kind": "window",
        }
    ]


def test_p5_reference_resolves_recommended_variant_without_pending_registration() -> None:
    registry = ParameterFamilyRegistry(families=[_window_family()])
    p1 = _payload(recommended=True)
    payload = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=p1,
        p5_candidates=[
            {
                "source_research": "p1",
                "research_id": p1["research_id"],
                "family_id": "family_a",
                "parameter": 20,
                "status": "incremental_rejected",
                "candidate_key": "p1_family_a_p20",
            }
        ],
        previous=_payload(factor_ids=["old_factor"], previous=True),
    )

    assert payload["pending_tasks"] == []
    assert payload["catalog_delta"]["new_factor_ids"] == ["factor_a_20d"]
    assert payload["catalog_delta"]["removed_factor_ids"] == ["old_factor"]


def test_catalog_delta_is_preserved_when_snapshot_is_unchanged() -> None:
    registry = ParameterFamilyRegistry(families=[_window_family()])
    first = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=_payload(),
    )
    first["catalog_delta"] = {
        "mode": "incremental",
        "previous_research_id": "sha256:" + "9" * 64,
        "new_factor_ids": ["factor_a_20d"],
        "removed_factor_ids": ["old_factor"],
    }

    rebuilt = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=_payload(),
        previous=first,
    )

    assert rebuilt["catalog_delta"] == first["catalog_delta"]


def test_recommended_unregistered_parameter_generates_registration_task() -> None:
    registry = ParameterFamilyRegistry(families=[_window_family()])
    p1 = {
        "research_id": "sha256:" + "5" * 64,
        "families": {
            "family_a": {
                "recommendation_status": "recommended",
                "recommended_parameter": 40,
                "recommended_variant_id": "family_a__p_40",
            }
        },
    }
    payload = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=p1,
    )

    assert payload["pending_tasks"] == [
        {
            "task_id": "factor_registration:window:family_a",
            "kind": "factor_registration",
            "status": "pending",
            "family_id": "family_a",
            "family_kind": "window",
            "research_id": p1["research_id"],
            "recommended_parameter": 40,
        }
    ]


def test_unclassified_numeric_factor_generates_blocked_task_and_negative_knowledge() -> None:
    registry = ParameterFamilyRegistry(unclassified_factor_ids=["factor_a_20d"])
    payload = _build(
        factor_ids=["factor_a_20d"],
        registry=registry,
        p1=_payload(),
    )

    assert payload["pending_tasks"][0]["kind"] == "classification"
    assert payload["pending_tasks"][0]["status"] == "blocked"
    assert payload["negative_knowledge"][0]["reasons"] == ["classification_unclassified"]
