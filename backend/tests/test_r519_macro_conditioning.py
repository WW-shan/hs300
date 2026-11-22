from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r519_macro_conditioning_preregistration.json"
OUTPUT = E / "r519_macro_conditioning_2026-11-22.json"
RESULT = E / "r519_macro_conditioning_2026-11-22.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r519_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R519"
    assert len(payload["conditioners"]) == 8
    assert payload["return_horizons"] == [5, 20, 60]
    assert payload["gates"]["newey_west_p_max"] == 0.1
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r519_outputs_are_diagnostic_only() -> None:
    for path in (OUTPUT, RESULT):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["round"] == "R519"
    assert payload["decision"]["active_version"] is None
