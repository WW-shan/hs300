from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r467_risk_budget_degeneracy_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r467_risk_budget_degeneracy.py"
OUTPUT = E / "r467_risk_budget_degeneracy_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r467_degeneracy", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r467_preregistration_freezes_degeneracy_rule() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R467"
    assert _sha(PREREG) == sidecar
    assert payload["degeneracy"]["require_max_weight_above_inverse_count"] is True
    assert payload["degeneracy"]["minimum_distinct_weight_vectors"] == 3
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True


def test_r467_degeneracy_is_detected() -> None:
    module = _module()
    equal_path = [[0.25, 0.25, 0.25, 0.25] for _ in range(10)]
    varied_path = [[0.30, 0.20, 0.25, 0.25], [0.35, 0.15, 0.25, 0.25], [0.28, 0.22, 0.25, 0.25]]
    assert module.weight_path_is_degenerate(equal_path, max_weight=0.25) is True
    assert module.weight_path_is_degenerate(varied_path, max_weight=0.40) is False


def test_r467_outputs_are_append_only_and_confirmation_only() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["source_round"] == "R466"
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r467_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r467.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")
