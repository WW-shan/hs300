from __future__ import annotations

import json
from pathlib import Path

from app.factor_research.bootstrap import bootstrap_core_research
from app.factor_research.catalog import build_catalog, save_catalog
from app.factor_research.fingerprints import hash_token
from app.factor_research.models import FactorAuditRecord
from app.factor_research.workflow import (
    apply_factor_audit,
    ensure_watch_factor_set,
    select_pool_candidates,
)


def _audit_record(factor_id: str, data_class: str, cross_section: str) -> FactorAuditRecord:
    return FactorAuditRecord(
        factor_id=factor_id,
        available=True,
        data_class=data_class,
        cross_section_type=cross_section,
    )


def test_select_pool_candidates_uses_integrity_and_semantics() -> None:
    factors = {
        "price_stock": _audit_record("price_stock", "price", "stock_cross_sectional"),
        "flow_stock": _audit_record("flow_stock", "flow", "stock_cross_sectional"),
        "financial_stock": _audit_record("financial_stock", "financial", "stock_cross_sectional"),
        "industry_stock": _audit_record("industry_stock", "industry", "industry_cross_sectional"),
        "market_macro": _audit_record("market_macro", "macro", "market_level"),
        "failed_flow": _audit_record("failed_flow", "flow", "stock_cross_sectional"),
    }
    findings = {
        **{key: {"integrity": "pass"} for key in factors if key != "failed_flow"},
        "failed_flow": {"integrity": "unknown"},
    }

    selected = select_pool_candidates(factors, findings)

    assert [item.factor_id for item in selected["core_v1"]] == ["price_stock"]
    assert [item.factor_id for item in selected["sentiment_flow_v1"]] == ["flow_stock"]
    assert [item.factor_id for item in selected["financial_v1"]] == ["financial_stock"]
    assert [item.factor_id for item in selected["industry_v1"]] == ["industry_stock"]
    assert [item.factor_id for item in selected["regime_v1"]] == ["market_macro"]


def test_apply_factor_audit_updates_pools_and_creates_watch_version_idempotently(
    tmp_path: Path,
) -> None:
    bootstrap_core_research(
        tmp_path,
        core_members=[{
            "factor_id": "momentum_20d",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 1.0,
        }],
    )
    taxonomy_path = tmp_path / "factor_research" / "catalog" / "field_taxonomy.json"
    taxonomy_path.parent.mkdir(parents=True, exist_ok=True)
    taxonomy_path.write_text(json.dumps({
        "schema_version": 1,
        "records": [{
            "factor_id": "momentum_20d",
            "data_class": "price",
            "cross_section_type": "stock_cross_sectional",
            "pit_hint": "not_applicable",
            "asset_types": ["stock"],
            "source_field": "momentum_20d",
            "zero_is_missing": False,
            "direction": "high",
            "classification_confidence": 0.9,
            "evidence_refs": [{"path": "test", "sha256": "sha256:" + "a" * 64}],
        }],
    }), encoding="utf-8")
    save_catalog(tmp_path, build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z"))

    audit_id = "sha256:" + "b" * 64
    root = tmp_path / "factor_research" / "audits" / hash_token(audit_id)
    root.mkdir(parents=True)
    factors = {
        "momentum_20d": _audit_record(
            "momentum_20d", "price", "stock_cross_sectional"
        ).model_dump(mode="json"),
        "rsi_14": _audit_record("rsi_14", "sentiment", "stock_cross_sectional").model_dump(
            mode="json"
        ),
    }
    findings = {key: {"integrity": "pass"} for key in factors}
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "audit_id": audit_id,
        "generated_at": "2026-11-10T00:00:00Z",
        "factor_count": len(factors),
    }), encoding="utf-8")
    (root / "metrics.json").write_text(json.dumps({
        "audit_id": audit_id,
        "factors": factors,
    }), encoding="utf-8")
    (root / "findings.json").write_text(json.dumps(findings), encoding="utf-8")

    first = apply_factor_audit(tmp_path, audit_id)
    second = apply_factor_audit(tmp_path, audit_id)

    assert first.core_version == 2
    assert second.core_version == 2
    assert second.pool_revisions["core_v1"] == first.pool_revisions["core_v1"]
    head = json.loads((tmp_path / "factor_research" / "sets" / "core_v1" / "head.json").read_text())
    assert head["state"] == "watch"
    assert head["watch_version"] == 2
    assert head["active_version"] is None
    core_pool = json.loads(
        (tmp_path / "factor_research" / "pools" / "core_v1" / "revisions" / "r0002.json").read_text()
    )
    assert "momentum_20d" in {item["factor_id"] for item in core_pool["members"]}
    sentiment_pool = json.loads(
        (tmp_path / "factor_research" / "pools" / "sentiment_flow_v1" / "revisions" / "r0002.json").read_text()
    )
    assert {item["factor_id"] for item in sentiment_pool["members"]} == {"rsi_14"}


def test_ensure_watch_factor_set_is_idempotent_and_never_active(tmp_path: Path) -> None:
    bootstrap_core_research(
        tmp_path,
        core_members=[{
            "factor_id": "momentum_20d",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 1.0,
        }],
    )
    members = [{
        "factor_id": "momentum_20d",
        "direction": "high",
        "weight": 1.0,
        "rationale": "test watch candidate",
    }]

    first = ensure_watch_factor_set(
        tmp_path, "multi_dimension_v1", members, "create multi-dimension watch set"
    )
    second = ensure_watch_factor_set(
        tmp_path, "multi_dimension_v1", members, "create multi-dimension watch set"
    )

    assert first.version == 1
    assert second.version == 1
    head = json.loads(
        (tmp_path / "factor_research" / "sets" / "multi_dimension_v1" / "head.json").read_text()
    )
    assert head["state"] == "watch"
    assert head["active_version"] is None
