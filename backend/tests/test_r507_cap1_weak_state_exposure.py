from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r507_cap1_weak_state_exposure_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r507_cap1_weak_state_exposure.py"


def _module():
    spec = importlib.util.spec_from_file_location("r507_exposure", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r507_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R507"
    assert payload["exposure_variants"] == [0.5, 0.75, 1.0]
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r507_overlay_is_t1_and_bounded() -> None:
    module = _module()
    curve = [
        {"date": "2025-10-14", "value": 100.0},
        {"date": "2025-10-15", "value": 101.0},
        {"date": "2025-10-16", "value": 102.0},
    ]
    output = module.apply_exposure(curve, {"2025-10-14": True, "2025-10-15": False}, 0.5)
    assert output[0]["value"] == 1.0
    assert output[1]["value"] < 1.01
    assert all(
        0.0 <= module.exposure_for_day(day, weak, 0.5) <= 1.0
        for day, weak in [("x", True), ("y", False)]
    )


def test_r507_outputs_are_confirmation_only() -> None:
    output = E / "r507_cap1_weak_state_exposure_2026-11-22.json"
    result = E / "r507_cap1_weak_state_exposure_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is True
    assert payload["decision"]["active_version"] is None
