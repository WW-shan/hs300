from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r506_r499_cap1_confirmation_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r506_r499_cap1_confirmation.py"


def _module():
    spec = importlib.util.spec_from_file_location("r506_cap1", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r506_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R506"
    assert payload["selected_variant"] == "cap_1"
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r506_weights_and_cap() -> None:
    module = _module()
    weights = module.weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    assert weights["value_quality"] == 0.05
    assert module.fixed_cap() == 1


def test_r506_outputs_are_confirmation_only() -> None:
    output = E / "r506_r499_cap1_confirmation_2026-11-22.json"
    result = E / "r506_r499_cap1_confirmation_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_performed"] is False
    assert payload["selected_variant"] == "cap_1"
    assert payload["decision"]["active_version"] is None
