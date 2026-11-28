from __future__ import annotations

import hashlib
from pathlib import Path

from app.factor_research.standard_validation import (
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    plot_parameter_artifacts,
)


def _gbm() -> dict:
    return {
        "status": "complete",
        "path_count": 100,
        "scenarios": 4,
        "cost_multipliers": [2, 3, 5],
        "same_path_benchmark": True,
        "deterministic_replay": True,
        "no_leverage": True,
        "max_exposure": 0.99,
        "summary": {
            "neutral": {"excess_p05": -0.12, "excess_p50": -0.03, "mdd_p50": -0.18},
            "bull": {"excess_p05": -0.10, "excess_p50": -0.02, "mdd_p50": -0.15},
            "bear": {"excess_p05": -0.22, "excess_p50": -0.05, "mdd_p50": -0.24},
            "high_volatility": {"excess_p05": -0.30, "excess_p50": -0.08, "mdd_p50": -0.31},
        },
        "cost_summary": {
            "2": {"annual_return_p50": 0.01, "mdd_p50": -0.2},
            "3": {"annual_return_p50": -0.02, "mdd_p50": -0.22},
            "5": {"annual_return_p50": -0.05, "mdd_p50": -0.25},
        },
    }


def _artifacts() -> list[dict]:
    return [
        {"kind": "gbm_distribution", "path": "a.png", "sha256": "a" * 64},
        {"kind": "gbm_constraints", "path": "b.png", "sha256": "b" * 64},
        {"kind": "parameter_paths", "path": "c.png", "sha256": "c" * 64},
    ]


def _parameter_mc() -> dict:
    return {
        "status": "complete",
        "selection_policy": "train_select_validation_confirm",
        "grid_size": 4,
        "selected": {"positions": 10, "hold_days": 60},
        "recent_used_for_selection": False,
        "constraints": {"validation_confirmed": True},
        "points": [
            {"parameter": 1, "train_return": 0.09, "validation_return": 0.02, "mdd": -0.12},
            {"parameter": 2, "train_return": 0.11, "validation_return": 0.01, "mdd": -0.10},
            {"parameter": 3, "train_return": 0.08, "validation_return": 0.04, "mdd": -0.18},
            {"parameter": 4, "train_return": 0.12, "validation_return": -0.01, "mdd": -0.09},
        ],
        "bootstrap": {"selected": {"p05": -0.04, "p50": 0.12, "p95": 0.28}},
    }


def test_standard_receipt_requires_project_formal_gbm() -> None:
    receipt = build_standard_validation_receipt(_gbm(), _parameter_mc(), artifacts=_artifacts())
    missing = build_standard_validation_receipt(
        {**_gbm(), "status": "not_applicable"}, _parameter_mc(), artifacts=_artifacts()
    )
    ok = check_standard_validation_constraints(receipt, research_type="project_formal")
    bad = check_standard_validation_constraints(missing, research_type="project_formal")
    assert ok["pass"] is True
    assert bad["pass"] is False
    assert "gbm_status_complete" in bad["errors"]


def test_standard_receipt_rejects_leverage_and_incomplete_costs() -> None:
    receipt = build_standard_validation_receipt(_gbm(), _parameter_mc(), artifacts=_artifacts())
    receipt["gbm"]["max_exposure"] = 1.2
    receipt["gbm"]["cost_multipliers"] = [2, 3]
    result = check_standard_validation_constraints(receipt, research_type="project_formal")
    assert result["pass"] is False
    assert "gbm_max_exposure_le_1" in result["errors"]
    assert "gbm_costs_2_3_5" in result["errors"]


def test_standard_receipt_rejects_recent_parameter_selection() -> None:
    parameter = {**_parameter_mc(), "recent_used_for_selection": True}
    receipt = build_standard_validation_receipt(_gbm(), parameter, artifacts=_artifacts())
    result = check_standard_validation_constraints(receipt, research_type="project_formal")
    assert result["pass"] is False
    assert "parameter_recent_not_used_for_selection" in result["errors"]


def test_standard_plots_are_materialized_as_png(tmp_path: Path) -> None:
    gbm_dist = tmp_path / "gbm_distribution.png"
    gbm_constraints = tmp_path / "gbm_constraints.png"
    plot_gbm_distribution(_gbm()["summary"], gbm_dist)
    plot_gbm_constraints(_gbm()["cost_summary"], gbm_constraints)
    parameter_paths, pareto, bootstrap = plot_parameter_artifacts(
        _parameter_mc()["points"],
        _parameter_mc()["bootstrap"],
        tmp_path,
        prefix="r550_parameter",
    )
    for path in (gbm_dist, gbm_constraints, parameter_paths, pareto, bootstrap):
        assert path.is_file()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(gbm_dist.read_bytes()).hexdigest()


def test_gbm_summary_aggregates_paths_and_costs() -> None:
    from app.factor_research.standard_validation import summarize_gbm_paths

    paths = []
    for path_id in range(2):
        observations = []
        for beta in (0.0, 0.7):
            for cost in (2, 3, 5):
                observations.append(
                    {
                        "scenario_id": "neutral",
                        "beta": beta,
                        "path_id": path_id,
                        "cost_multiplier": cost,
                        "excess_return": -0.01 - 0.002 * cost,
                        "strategy_total_return": 0.05 - 0.002 * cost,
                        "strategy_annual_return": 0.04 - 0.002 * cost,
                        "strategy_max_drawdown": -0.15 - 0.01 * cost,
                        "strategy_sharpe": 0.5,
                        "strategy_avg_exposure": 0.8,
                        "strategy_max_exposure": 0.9,
                        "strategy_turnover": 1.0,
                        "benchmark_total_return": 0.03,
                        "benchmark_max_exposure": 1.0,
                    }
                )
        paths.append({"observations": observations})
    summary = summarize_gbm_paths(
        paths,
        path_count=2,
        scenarios={"neutral"},
        costs=(2, 3, 5),
        deterministic_replay=True,
    )
    assert summary["constraints"]["complete"] is True
    assert summary["max_exposure"] <= 1.0
    assert summary["summary"]["neutral"]["excess_p50"] < 0
    assert set(summary["cost_summary"]) == {"2", "3", "5"}
