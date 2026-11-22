from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r482_r480_volatility_scaling_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r482_r480_volatility_scaling.py"


def _module():
    spec = importlib.util.spec_from_file_location("r482_vol_overlay", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r482_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R482"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["benchmark"] == "000300.SH"
    assert all(spec.get("max_weight", 1.0) <= 1.0 for spec in payload["overlays"].values())
    assert payload["output_policy"]["active_version"] is None
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r482_overlay_weights_are_trailing_and_bounded() -> None:
    module = _module()
    curve = [
        {"date": "2024-01-02", "value": 1.0},
        {"date": "2024-01-03", "value": 1.01},
        {"date": "2024-01-04", "value": 1.00},
        {"date": "2024-01-05", "value": 1.02},
    ]
    weights = module.overlay_weights(curve, {"type": "vol_target", "annual_target": 0.15, "lookback_days": 2, "min_weight": 0.2, "max_weight": 1.0})
    assert len(weights) == len(curve)
    assert all(0.0 <= value <= 1.0 for value in weights)
    assert weights[0] == 0.2
    assert weights[-1] <= 1.0


def test_r482_outputs_are_bounded_and_rejected_unless_all_gates_pass() -> None:
    output = E / "r482_r480_volatility_scaling_2026-11-22.json"
    result = E / "r482_r480_volatility_scaling_2026-11-22.md"
    for path in (output, result):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["benchmark"] == "000300.SH"
    for row in payload["rows"]:
        assert 0.0 <= row["test"]["weight_min"] <= row["test"]["weight_max"] <= 1.0
        assert 0.0 <= row["recent"]["weight_min"] <= row["recent"]["weight_max"] <= 1.0
    assert (payload["decision"]["status"] == "watch_candidate") == bool(payload["gate"]["pass"])
    assert payload["decision"]["active_version"] is None
