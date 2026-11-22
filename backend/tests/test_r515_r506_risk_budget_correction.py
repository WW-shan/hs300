from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r515_r506_risk_budget_correction_preregistration.json"
OUTPUT = E / "r515_r506_risk_budget_correction_2026-11-22.json"
RESULT = E / "r515_r506_risk_budget_correction_2026-11-22.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r515_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R515"
    assert payload["source_round"] == "R514"
    assert len(payload["overlays"]) == 7
    assert payload["confirmation_gates"]["no_leverage"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r515_outputs_are_confirmation_only() -> None:
    for path in (OUTPUT, RESULT):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["round"] == "R515"
    assert payload["decision"]["active_version"] is None
