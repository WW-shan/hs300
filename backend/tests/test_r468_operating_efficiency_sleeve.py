from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r468_operating_efficiency_sleeve_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py"
OUTPUT = E / "r468_operating_efficiency_sleeve_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r468_op_eff", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r468_preregistration_freezes_factors_and_costs() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R468"
    assert _sha(PREREG) == sidecar
    assert payload["factors"] == {
        "inventory_turnover_improvement_yoy": "high",
        "inventory_growth_minus_cogs_growth": "low",
    }
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["cost_main"] == "2x"
    assert payload["execution"]["cost_stress"] == "3x"
    assert payload["execution"]["cost_diagnostic"] == "5x"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True


def test_r468_samples_are_deterministic_and_single_factor() -> None:
    module = _module()
    first = module.generate_samples()
    second = module.generate_samples()
    assert first == second
    assert len(first) == 108
    for row in first:
        assert len(row["weights"]) == 1
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["direction"] in {"high", "low"}
        assert row["max_positions"] in {10, 20}
        assert row["max_hold_days"] in {20, 30, 40}
        assert row["entry_score"] in {60.0, 70.0, 80.0}
        assert row["exit_score"] in {30.0, 40.0, 50.0}


def test_r468_outputs_are_confirmation_only() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r468_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r468.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")


def test_r468_stress_cost_changes_the_curve() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selected"]["test"]["stats"]["annual_return"] != payload["selected"]["test_3x"]["stats"]["annual_return"]
    assert payload["selected"]["test_3x"]["stats"]["annual_return"] > 0.0
