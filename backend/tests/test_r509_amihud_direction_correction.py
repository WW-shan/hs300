from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r509_amihud_direction_correction_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r509_amihud_direction_correction.py"


def _module():
    spec = importlib.util.spec_from_file_location("r509_amihud", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r509_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R509"
    assert payload["candidate"] == "amihud_20d"
    assert payload["corrected_direction"] == "high"
    assert payload["added_weight_grid"] == [0.05, 0.1]
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r509_only_amihud_direction_changes() -> None:
    module = _module()
    weights = module.adjunct_weights(0.05)
    assert abs(sum(weights.values()) - 1.0) < 1e-12
    assert weights["amihud_20d"] == 0.05
    assert module.direction() == "high"


def test_r509_outputs_are_confirmation_only() -> None:
    output = E / "r509_amihud_direction_correction_2026-11-22.json"
    result = E / "r509_amihud_direction_correction_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["candidate"] == "amihud_20d"
    assert payload["corrected_direction"] == "high"
    assert payload["decision"]["active_version"] is None
