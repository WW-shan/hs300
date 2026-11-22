from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r499_weak_state_value_quality_tilt_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r499_weak_state_value_quality_tilt.py"


def _module():
    spec = importlib.util.spec_from_file_location("r499_tilt", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r499_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R499"
    assert payload["weak_value_quality_candidates"] == [0.0, 0.025, 0.05]
    assert payload["weak_state"]["score_threshold"] == 39.0
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["source_r498"] == "r498_drawdown_conditional_attribution_2026-11-22.json"
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r499_weights_are_normalized_and_only_weak_value_quality_changes() -> None:
    module = _module()
    for weight in [0.0, 0.025, 0.05]:
        weights = module.weak_weights(weight)
        assert abs(sum(weights.values()) - 1.0) < 1e-12
        assert weights["value_quality"] == weight
        assert all(
            0.0 < value < 1.0 for member, value in weights.items() if member != "value_quality"
        )
    base = module.base_weights()
    assert abs(sum(base.values()) - 1.0) < 1e-12
    assert base["value_quality"] == 0.1


def test_r499_outputs_are_confirmation_only() -> None:
    output = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
    result = E / "r499_weak_state_value_quality_tilt_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
