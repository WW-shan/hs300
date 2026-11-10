from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.factor_research.taxonomy import validate_field_taxonomy

ROOT = Path(__file__).resolve().parents[3]


def test_alt_hs300_taxonomy_covers_every_extension_field() -> None:
    config_path = ROOT / "data" / "ext_data" / "alt_hs300" / "config.json"
    taxonomy_path = ROOT / "data" / "factor_research" / "catalog" / "field_taxonomy.json"
    if not config_path.exists():
        pytest.skip("optional local alt_hs300 extension data is not present")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    expected = {f"ext_alt_hs300_{field['name']}" for field in config["fields"]}

    errors = validate_field_taxonomy(taxonomy, expected_factor_ids=expected)

    assert errors == []
    assert len(expected) == 109
    for record in taxonomy["records"]:
        for evidence in record["evidence_refs"]:
            path = ROOT / evidence["path"]
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            assert evidence["sha256"] == digest


def test_taxonomy_rejects_missing_evidence_and_unknown_enums() -> None:
    payload = {
        "schema_version": 1,
        "records": [{
            "factor_id": "ext_demo_x",
            "data_class": "mystery",
            "cross_section_type": "sectorish",
            "pit_hint": "probably",
            "asset_types": ["stock"],
            "source_field": "x",
            "zero_is_missing": False,
            "classification_confidence": 0.5,
            "evidence_refs": [],
        }],
    }

    errors = validate_field_taxonomy(payload)

    assert any("data_class" in item for item in errors)
    assert any("cross_section_type" in item for item in errors)
    assert any("pit_hint" in item for item in errors)
    assert any("evidence_refs" in item for item in errors)
