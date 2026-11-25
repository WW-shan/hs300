from __future__ import annotations

from pathlib import Path


def _runner():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/research_r546_vol12_confirmation.py"
    spec = importlib.util.spec_from_file_location("research_r546_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scientific_design_freezes_vol12_only() -> None:
    runner = _runner()
    design = runner.scientific_design()
    assert design["variant"]["id"] == "vol_12"
    assert design["variant"]["target"] == 0.12
    assert design["variant"]["floor"] == 0.5
    assert design["confirmation_runs"] == 13
    assert design["formal_runs"] == 13
    assert design["bootstrap_runs"] == 5000
    assert design["recent_is_independent_oos"] is False
    assert design["execution"]["leverage"] == "none"
    assert design["execution"]["max_exposure_pct"] == 1.0
    assert design["output_policy"]["active_version"] is None


def test_candidate_gate_watch_only_and_5x_warning() -> None:
    runner = _runner()
    gate = runner.candidate_gate(
        full={"annual_return": 0.12, "sharpe": 1.0, "max_drawdown": -0.12},
        recent={"total_return": 0.02},
        validation={"annual_return": 0.03},
        fresh_values=[0.1, 0.2, 0.03, 0.04, -0.01, -0.02],
        stress_3x={
            "annual_return": 0.09,
            "max_drawdown": -0.15,
            "recent_total_return": 0.0,
        },
        warning_5x={"annual_return": -0.2, "max_drawdown": -0.2},
    )
    assert gate["core_pass"] is True
    assert gate["stress_pass"] is True
    assert gate["conclusion"] == "watch_only"
    assert gate["five_x_hard_rejection"] is False
    assert gate["five_x_warnings"]


def test_candidate_gate_rejects_validation_failure() -> None:
    runner = _runner()
    gate = runner.candidate_gate(
        full={"annual_return": 0.12, "sharpe": 1.0, "max_drawdown": -0.12},
        recent={"total_return": 0.02},
        validation={"annual_return": -0.01},
        fresh_values=[0.1, 0.2, 0.03, 0.04, -0.01, -0.02],
        stress_3x={
            "annual_return": 0.09,
            "max_drawdown": -0.15,
            "recent_total_return": 0.0,
        },
        warning_5x={"annual_return": 0.01, "max_drawdown": -0.15},
    )
    assert gate["conclusion"] == "rejected"
    assert "validation_annual_positive" in gate["failed_gates"]
