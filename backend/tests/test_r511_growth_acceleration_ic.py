from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r511_growth_acceleration_ic_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r511_growth_acceleration_ic.py"


def _module():
    spec = importlib.util.spec_from_file_location("r511_growth", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r511_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R511"
    assert set(payload["composites"]) == {"growth_acceleration", "quality_confirmed_growth"}
    assert payload["return_horizons"] == [5, 20, 60]
    assert payload["gates"]["yearly_sign_consistency_min"] == 0.8
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r511_composite_weights_normalized() -> None:
    module = _module()
    for name in ("growth_acceleration", "quality_confirmed_growth"):
        weights = module.composite_weights(name)
        assert abs(sum(weights.values()) - 1.0) < 1e-12
    accel = module.acceleration_frame(
        __import__("polars").DataFrame(
            {
                "symbol": ["A", "A"],
                "period_end": ["2024-03-31", "2024-06-30"],
                "revenue_yoy": [0.1, 0.2],
                "net_income_yoy": [0.05, 0.15],
            }
        )
    )
    assert "growth_acceleration" in accel.columns
    assert accel["growth_acceleration"][1] is not None


def test_r511_outputs_are_diagnostic_only() -> None:
    output = E / "r511_growth_acceleration_ic_2026-11-22.json"
    result = E / "r511_growth_acceleration_ic_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["decision"]["active_version"] is None
