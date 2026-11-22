from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r525_unified_membership_generality_audit_preregistration.json"
OUTPUT = E / "r525_unified_membership_generality_audit_2026-11-23.json"
RESULT = E / "r525_unified_membership_generality_audit_2026-11-23.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r525_preregistration_is_frozen_and_scope_is_read_only() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R525"
    assert payload["output_policy"]["active_version"] is None
    assert payload["output_policy"]["orders_generated"] is False
    assert payload["output_policy"]["production_enabled"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]


def test_r525_declares_both_data_vintages_explicitly() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert "daily HS300 membership" in payload["hypotheses"]["h1_r385_historical_membership"]
    assert "current official HS300 membership" in payload["hypotheses"]["h2_r506_current_membership"]
    assert "cannot be directly ranked" in payload["hypotheses"]["h3_not_rankable"]


def test_r525_outputs_when_present_are_research_only() -> None:
    if not OUTPUT.exists() or not RESULT.exists():
        return
    for path in (OUTPUT, RESULT):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["production_enabled"] is False
    assert payload["verdict"]["direct_winner"] is None
