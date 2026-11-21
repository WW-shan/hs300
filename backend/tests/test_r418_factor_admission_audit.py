from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r418_factor_admission_audit_preregistration.json"


def test_r418_freezes_admission_rules_and_fail_closed_policy() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R418"
    assert payload["rules"]["minimum_yearly_sign_consistency"] == 0.80
    assert payload["rules"]["require_recent_ic_positive"] is True
    assert payload["missing_metrics_policy"] == "fail_closed_no_admission"
    assert payload["output_policy"]["active_version"] is None


def test_r418_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r418_admitted_factors_are_fail_closed_and_have_all_metrics() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r418_factor_admission_audit_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["admitted_count"] == len(payload["admitted"])
    for factor in payload["admitted"]:
        row = payload["rows"][factor]
        assert all(row["checks"].values())
        assert row["yearly_sign_consistency"] >= 0.80
        assert row["recent_ic"] > 0
    for name in ["r418_factor_admission_audit_preregistration.json", "r418_factor_admission_audit_2026-11-22.json", "r418_factor_admission_audit_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
