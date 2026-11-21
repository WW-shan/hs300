from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"


def test_r433_covers_all_parameter_families() -> None:
    payload = json.loads((E / "r433_parameter_audit_2026-11-22.json").read_text(encoding="utf-8"))
    summary = payload["summary"]
    assert summary["registry_family_count"] == 62
    assert summary["covered_family_count"] == 62
    assert summary["missing_family_ids"] == []
    assert summary["recommendation_count"] == 11
    assert summary["rejected_count"] == 51
    assert payload["decision"]["active_version"] is None


def test_r433_sidecars() -> None:
    for name in ["r433_parameter_audit_preregistration.json", "r433_parameter_audit_2026-11-22.json", "r433_parameter_audit_2026-11-22.md"]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
