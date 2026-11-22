from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r508_r499_adjunct_screening_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r508_r499_adjunct_screening.py"


def _module():
    spec = importlib.util.spec_from_file_location("r508_adjunct", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r508_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R508"
    assert payload["added_weight_grid"] == [0.05, 0.1]
    assert len(payload["candidates"]) == 8
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r508_adjunct_weights_are_normalized() -> None:
    module = _module()
    base = module.base_weights()
    for candidate in json.loads(PREREG.read_text(encoding="utf-8"))["candidates"]:
        for added in (0.05, 0.1):
            weights = module.adjunct_weights(candidate, added)
            assert abs(sum(weights.values()) - 1.0) < 1e-12
            assert weights[candidate] == added
            assert weights["value_quality"] == base["value_quality"] * (1.0 - added)


def test_r508_outputs_are_confirmation_only() -> None:
    output = E / "r508_r499_adjunct_screening_2026-11-22.json"
    result = E / "r508_r499_adjunct_screening_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["decision"]["active_version"] is None
