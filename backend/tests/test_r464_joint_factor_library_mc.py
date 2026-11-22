from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r464_joint_factor_library_mc_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r464_joint_factor_library_mc.py"
OUTPUT = E / "r464_joint_factor_library_mc_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r464_joint_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r464_preregistration_freezes_scope_and_costs() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R464"
    assert _sha(PREREG) == sidecar
    assert set(payload["sleeves"]) == {"four_vq", "seven_vq", "four_vqs", "three_vq"}
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["cost_main"] == "2x"
    assert payload["execution"]["cost_stress"] == "3x"
    assert payload["execution"]["cost_diagnostic"] == "5x"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True


def test_r464_samples_are_deterministic_and_bounded() -> None:
    module = _module()
    first = module.generate_samples()
    second = module.generate_samples()
    assert first == second
    assert len(first) >= 97
    assert any(row["label"] == "r463_baseline" for row in first)
    for row in first:
        weights = row["weights"]
        assert weights
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert all(value >= 0.0 for value in weights.values())
        assert 0.0 < row["max_exposure_pct"] <= 1.0
        assert row["max_positions"] in {10, 20}
        assert row["max_hold_days"] in {20, 25, 30, 35, 40}
        assert row["entry_score"] in {60.0, 70.0, 80.0}
        assert row["exit_score"] in {30.0, 40.0, 50.0}


def test_r464_outputs_are_append_only_and_confirmation_only() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["paper_target_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r464_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r464.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")


def test_r464_no_signal_is_recorded_as_failed_sample() -> None:
    module = _module()
    failed = module._failed_metrics()
    assert failed["stats"]["annual_return"] < 0.0
    assert failed["stats"]["sharpe"] < 0.0
    assert failed["trades"] == 0
    assert failed["curve"] == []


def test_r464_output_retains_training_and_validation_evidence() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert "train" in payload["selected"]
    assert "validation" in payload["selected"]
    assert payload["selected"]["train"]["stats"]["annual_return"] is not None
    assert payload["selected"]["validation"]["stats"]["annual_return"] is not None
    for row in payload["samples"]:
        assert "train" in row
        assert "validation" in row
