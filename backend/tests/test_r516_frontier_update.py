from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r516_frontier_update_preregistration.json"
OUTPUT = E / "r516_frontier_update_2026-11-22.json"
RESULT = E / "r516_frontier_update_2026-11-22.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r516_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R516"
    assert set(payload["sources"]) == {"r510", "r511", "r512", "r513", "r514", "r515"}
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r516_outputs_are_consolidation_only() -> None:
    for path in (OUTPUT, RESULT):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["frontier"]["growth_passes"] == []
    assert payload["frontier"]["industry_condition_passes"] == []
    assert payload["frontier"]["r514_decision"] == "rejected"
    assert payload["frontier"]["r515_decision"] == "rejected"
    assert payload["decision"]["active_version"] is None
