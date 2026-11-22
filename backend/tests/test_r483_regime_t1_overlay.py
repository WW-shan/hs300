from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r483_regime_t1_overlay_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r483_regime_t1_overlay.py"


def _module():
    spec = importlib.util.spec_from_file_location("r483_regime", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r483_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R483"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["regime_source"]["use"] == "previous trading day row only"
    assert payload["benchmark"] == "000300.SH"
    assert payload["output_policy"]["active_version"] is None
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r483_t1_alignment_never_uses_same_day() -> None:
    module = _module()
    regime = {
        "2024-01-02": {"state": "weak", "phase": "ebb", "score": 10.0},
        "2024-01-03": {"state": "strong", "phase": "ignite", "score": 80.0},
    }
    assert module.lookup_previous_day("2024-01-03", regime) == regime["2024-01-02"]
    assert module.lookup_previous_day("2024-01-02", regime) is None


def test_r483_overlay_weights_are_bounded() -> None:
    module = _module()
    regime = {
        "2024-01-02": {"state": "weak", "phase": "ebb", "score": 10.0},
        "2024-01-03": {"state": "strong", "phase": "ignite", "score": 80.0},
    }
    curve = [
        {"date": "2024-01-02", "value": 1.0},
        {"date": "2024-01-03", "value": 1.01},
        {"date": "2024-01-04", "value": 1.02},
    ]
    weights = module.overlay_weights(curve, regime, {"type": "state_exclude", "states": ["weak"], "weight": 0.0})
    assert len(weights) == len(curve)
    assert all(0.0 <= value <= 1.0 for value in weights)


def test_r483_outputs_are_confirmation_only_and_bounded() -> None:
    output = E / "r483_regime_t1_overlay_2026-11-22.json"
    result = E / "r483_regime_t1_overlay_2026-11-22.md"
    for path in (output, result):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert payload["diagnostics"]["regime_shift_days"] == 1
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
    for row in payload["rows"]:
        assert 0.0 <= row["test"]["weight_min"] <= row["test"]["weight_max"] <= 1.0
