from __future__ import annotations

import json
from pathlib import Path

from app.factor_research.references import ReferenceResolver
from app.factor_research.strategy_audit import audit_strategy_payloads


def test_physical_columns_are_not_missing_factors() -> None:
    resolver = ReferenceResolver.from_current()
    assert resolver.resolve("amount").reference_kind == "physical_column"
    assert resolver.resolve("consecutive_limit_ups").reference_kind == "physical_column"


def test_unknown_reference_is_dangling() -> None:
    resolver = ReferenceResolver.from_current()
    assert resolver.resolve("not_a_real_factor").reference_kind == "dangling_reference"


def test_virtual_field_is_distinguished_from_factor() -> None:
    resolver = ReferenceResolver.from_current()
    resolved = resolver.resolve("turnover_z_60d")
    assert resolved.reference_kind in {"registered_factor", "virtual_scoring_field"}


def test_historical_alias_requires_explicit_evidence(tmp_path: Path) -> None:
    path = tmp_path / "factor_research" / "catalog" / "historical_aliases.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "aliases": [{
            "id": "old_factor",
            "classification": "historical_definition",
            "evidence_refs": [{"path": "evidence.json", "sha256": "sha256:abc"}],
        }]
    }), encoding="utf-8")
    resolver = ReferenceResolver.from_current(data_dir=tmp_path)
    resolved = resolver.resolve("old_factor")
    assert resolved.reference_kind == "historical_definition"
    assert resolved.factor_eligible is False


def test_strategy_audit_separates_physical_columns_and_dangling() -> None:
    resolver = ReferenceResolver.from_current()
    audit = audit_strategy_payloads(
        [{"id": "s1", "meta": {"scoring": {"amount": 1.0, "missing_x": 0.2}}}],
        resolver,
    )
    kinds = {item.reference_id: item.reference_kind for item in audit.references}
    assert kinds["amount"] == "physical_column"
    assert kinds["missing_x"] == "dangling_reference"
    assert "missing_x" in audit.dangling
