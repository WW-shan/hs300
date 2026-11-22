from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r514_r506_dynamic_risk_budget_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r514_r506_dynamic_risk_budget.py"


def _module():
    spec = importlib.util.spec_from_file_location("r514_risk", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r514_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R514"
    assert payload["source_r506"] == "r506_r499_cap1_confirmation_2026-11-22.json"
    assert len(payload["overlays"]) == 7
    assert all(0.0 <= spec.get("max_weight", 1.0) <= 1.0 for spec in payload["overlays"].values())
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r514_overlay_weights_bounded_and_past_only() -> None:
    module = _module()
    curve = [
        {"date": "2025-10-14", "value": 100.0},
        {"date": "2025-10-15", "value": 101.0},
        {"date": "2025-10-16", "value": 99.0},
        {"date": "2025-10-17", "value": 98.0},
    ]
    spec = {
        "type": "vol_target",
        "annual_target": 0.10,
        "lookback_days": 2,
        "min_weight": 0.25,
        "max_weight": 1.0,
    }
    weights = module.overlay_weights(curve, spec)
    assert len(weights) == len(curve)
    assert all(0.25 <= value <= 1.0 for value in weights)


def test_r514_outputs_are_confirmation_only() -> None:
    output = E / "r514_r506_dynamic_risk_budget_2026-11-22.json"
    result = E / "r514_r506_dynamic_risk_budget_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["decision"]["active_version"] is None
