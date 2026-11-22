from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r466_sleeve_risk_budget_mc_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r466_sleeve_risk_budget_mc.py"
OUTPUT = E / "r466_sleeve_risk_budget_mc_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r466_sleeve_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r466_preregistration_freezes_universes_and_costs() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R466"
    assert _sha(PREREG) == sidecar
    assert set(payload["universes"]) == {"core5", "seven_vq", "compact4", "extended9"}
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["cost_main"] == "2x"
    assert payload["execution"]["cost_stress"] == "3x"
    assert payload["execution"]["cost_diagnostic"] == "5x"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True


def test_r466_variants_are_deterministic_and_bounded() -> None:
    module = _module()
    first = module.generate_variants()
    second = module.generate_variants()
    assert first == second
    assert len(first) == 160
    for row in first:
        weights = row["weights"]
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert all(value >= 0.0 for value in weights.values())
        assert max(weights.values()) <= row["max_weight"] + 1e-9


def test_r466_risk_budget_weights_are_long_only_and_capped() -> None:
    module = _module()
    returns = np.array([
        [0.01, 0.02, -0.01],
        [0.00, 0.01, 0.00],
        [-0.02, 0.03, 0.01],
        [0.01, -0.01, 0.02],
        [0.03, 0.00, 0.01],
    ])
    for method in ("inverse_vol", "erc", "min_variance"):
        weights = module.estimate_weights(returns, method, max_weight=0.60, min_weight=0.02)
        assert len(weights) == 3
        assert abs(sum(weights) - 1.0) < 1e-9
        assert all(value >= 0.02 - 1e-9 for value in weights)
        assert max(weights) <= 0.60 + 1e-9


def test_r466_outputs_are_confirmation_only() -> None:
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


def test_r466_runner_refuses_existing_output(tmp_path, monkeypatch) -> None:
    module = _module()
    output = tmp_path / "r466.json"
    output.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(module, "OUTPUT", output)
    try:
        module.ensure_outputs_absent()
    except RuntimeError as exc:
        assert "refusing overwrite" in str(exc)
    else:
        raise AssertionError("runner would overwrite evidence")


def test_r466_dynamic_weights_do_not_use_current_return() -> None:
    module = _module()
    dates = [f"2026-01-{day:02d}" for day in range(1, 9)]
    base = [1.0, 1.01, 1.02, 1.015, 1.03, 1.04, 1.035, 1.05]
    curves = {
        "a": [{"date": day, "value": value} for day, value in zip(dates, base, strict=True)],
        "b": [{"date": day, "value": value} for day, value in zip(dates, [1.0] * len(dates), strict=True)],
    }
    variant = {
        "id": "test",
        "universe": "test",
        "sleeve_names": ["a", "b"],
        "method": "inverse_vol",
        "lookback": 2,
        "rebalance": 1,
        "max_weight": 1.0,
        "weights": {"a": 0.5, "b": 0.5},
    }
    first = module.overlay_curve(curves, variant, min_weight=0.0, turnover_cost_bps=0.0)
    changed = dict(curves)
    changed["a"] = [dict(item) for item in curves["a"]]
    changed["a"][-1]["value"] = 99.0
    second = module.overlay_curve(changed, variant, min_weight=0.0, turnover_cost_bps=0.0)
    assert first[:-1] == second[:-1]
