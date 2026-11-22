from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r470_value_quality_safety_standalone_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r470_value_quality_safety_standalone.py"
OUTPUT = E / "r470_value_quality_safety_standalone_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r470_vqs", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r470_preregistration_freezes_single_factor() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R470"
    assert _sha(PREREG) == sidecar
    assert payload["factor"] == "value_quality_safety"
    assert payload["direction"] == "high"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True


def test_r470_samples_are_deterministic_and_single_factor() -> None:
    module = _module()
    first = module.generate_samples()
    second = module.generate_samples()
    assert first == second
    assert len(first) == 54
    for row in first:
        assert row["weights"] == {"value_quality_safety": 1.0}
        assert row["max_positions"] in {10, 20}
        assert row["max_hold_days"] in {20, 30, 40}
        assert row["entry_score"] in {60.0, 70.0, 80.0}
        assert row["exit_score"] in {30.0, 40.0, 50.0}


def test_r470_outputs_are_confirmation_only() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r470_stress_cost_is_distinct() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selected"]["test"]["stats"]["annual_return"] != payload["selected"]["test_3x"]["stats"]["annual_return"]
    assert payload["selected"]["test_3x"]["stats"]["annual_return"] > 0.0
