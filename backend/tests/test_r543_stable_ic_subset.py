from __future__ import annotations

from pathlib import Path

import pytest


def _runner():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/research_r543_stable_ic_subset.py"
    spec = importlib.util.spec_from_file_location("research_r543_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_stable_selection_uses_train_and_validation_only() -> None:
    runner = _runner()
    rows = [
        {"family_id": "keep", "train_ic": 0.01, "validation_ic": 0.02, "recent_ic": -1.0},
        {"family_id": "fail_train", "train_ic": -0.01, "validation_ic": 0.02, "recent_ic": 1.0},
        {"family_id": "fail_validation", "train_ic": 0.01, "validation_ic": 0.0, "recent_ic": 1.0},
        {"family_id": "missing", "train_ic": None, "validation_ic": 0.02, "recent_ic": 1.0},
    ]
    assert runner.select_stable_factors(rows) == ["keep"]


def test_fresh_start_gate_requires_four_positive_and_positive_median() -> None:
    runner = _runner()
    passed = runner.fresh_start_gate([0.1, 0.2, -0.01, 0.03, 0.04, -0.02])
    failed = runner.fresh_start_gate([0.1, 0.2, -0.01, -0.03, -0.04, 0.02])
    assert passed == {
        "positive_count": 4,
        "median": pytest.approx(0.035),
        "pass": True,
    }
    assert failed["positive_count"] == 3
    assert failed["pass"] is False


def test_candidate_gate_on_boundary_and_5x_warning_only() -> None:
    runner = _runner()
    result = runner.candidate_gate(
        full={"annual_return": 0.10, "sharpe": 0.90, "max_drawdown": -0.20},
        recent={"total_return": 0.0001},
        validation={"annual_return": 0.0001},
        fresh_values=[0.1, 0.2, 0.03, 0.04, -0.01, -0.02],
        stress_3x={
            "annual_return": 0.08,
            "max_drawdown": -0.22,
            "recent_total_return": -0.03,
        },
        warning_5x={"annual_return": -0.50, "max_drawdown": -0.50},
    )
    assert result["core_pass"] is True
    assert result["stress_pass"] is True
    assert result["conclusion"] == "watch_only"
    assert result["five_x_hard_rejection"] is False
    assert result["five_x_warnings"]


def test_candidate_gate_rejects_sharpe_below_research_floor() -> None:
    runner = _runner()
    result = runner.candidate_gate(
        full={"annual_return": 0.15, "sharpe": 0.89, "max_drawdown": -0.1},
        recent={"total_return": 0.02},
        validation={"annual_return": 0.02},
        fresh_values=[0.1, 0.2, 0.03, 0.04, -0.01, -0.02],
        stress_3x={
            "annual_return": 0.09,
            "max_drawdown": -0.15,
            "recent_total_return": 0.0,
        },
        warning_5x={"annual_return": 0.01, "max_drawdown": -0.16},
    )
    assert result["conclusion"] == "rejected"
    assert "full_sharpe_min" in result["failed_gates"]


def test_scientific_design_freezes_seven_factors_and_thirteen_runs() -> None:
    runner = _runner()
    design = runner.scientific_design()
    assert len(design["members"]) == 7
    assert design["formal_runs"] == 13
    assert runner.R542_RUNNER.is_file()
    assert design["selection_rule"] == "train_ic_gt_0_and_validation_ic_gt_0"
    assert design["recent_is_independent_oos"] is False
    assert design["execution"]["leverage"] == "none"
    assert design["execution"]["max_exposure_pct"] == 1.0
    assert design["execution"]["five_x_policy"] == "warning_only"
    assert design["output_policy"]["active_version"] is None
