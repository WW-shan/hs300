from __future__ import annotations

from pathlib import Path


def _runner():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/research_r544_stable7_parameter_mc.py"
    spec = importlib.util.spec_from_file_location("research_r544_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parameter_grid_has_108_unique_points_and_default() -> None:
    runner = _runner()
    grid = runner.parameter_grid()
    assert len(grid) == 108
    assert len({runner.parameter_key(row) for row in grid}) == 108
    assert {
        "max_positions": 10,
        "max_hold_days": 60,
        "industry_cap": 3,
        "stop_variant": "stop_12",
        "stop_loss": -0.12,
    } in grid


def test_training_eligibility_boundaries() -> None:
    runner = _runner()
    base = {
        "stats": {
            "annual_return": 0.10,
            "sharpe": 0.90,
            "max_drawdown": -0.20,
        },
        "trade_count": 20,
    }
    assert runner.training_eligible(base) is True
    assert runner.training_eligible({
        **base,
        "stats": {**base["stats"], "sharpe": 0.89},
    }) is False


def test_plateau_prefers_broad_region_over_isolated_spike() -> None:
    runner = _runner()
    rows = runner.parameter_grid()
    for row in rows:
        row["stats"] = {
            "annual_return": 0.11,
            "sharpe": 0.95,
            "max_drawdown": -0.16,
        }
        row["trade_count"] = 30
    spike = next(
        row for row in rows
        if runner.parameter_key(row) == (15, 60, 3, "stop_12")
    )
    spike["stats"] = {
        "annual_return": 0.30,
        "sharpe": 1.50,
        "max_drawdown": -0.10,
    }
    for row in runner.plateau_neighbors(spike, rows):
        row["stats"] = {
            "annual_return": 0.02,
            "sharpe": 0.20,
            "max_drawdown": -0.30,
        }
    selected = runner.select_plateau(rows, limit=5)
    assert runner.parameter_key(selected[0]) != runner.parameter_key(spike)
    assert len({runner.parameter_key(row) for row in selected}) == 5


def test_bootstrap_finalist_uses_p05_then_plateau_quality() -> None:
    runner = _runner()
    finalists = [
        {
            "parameters": {"id": "low"},
            "max_positions": 10,
            "max_hold_days": 60,
            "industry_cap": 3,
            "stop_variant": "stop_12",
            "bootstrap": {"total_p05": 0.10},
            "neighbor_stats": {
                "median_sharpe": 1.10,
                "median_annual": 0.12,
            },
            "default_distance": 2,
        },
        {
            "parameters": {"id": "high"},
            "max_positions": 15,
            "max_hold_days": 60,
            "industry_cap": 3,
            "stop_variant": "stop_12",
            "bootstrap": {"total_p05": 0.20},
            "neighbor_stats": {
                "median_sharpe": 0.95,
                "median_annual": 0.11,
            },
            "default_distance": 1,
        },
    ]
    assert runner.select_bootstrap_finalist(finalists)["parameters"]["id"] == "high"


def test_scientific_design_freezes_121_formal_runs() -> None:
    runner = _runner()
    design = runner.scientific_design()
    assert tuple(design["members"]) == runner.EXPECTED_MEMBERS
    assert design["grid_points"] == 108
    assert design["confirmation_runs"] == 13
    assert design["formal_runs"] == 121
    assert design["bootstrap_runs"] == 5000
    assert design["bootstrap_block"] == 20
    assert design["recent_is_independent_oos"] is False
    assert design["execution"]["five_x_policy"] == "warning_only"
    assert design["output_policy"]["active_version"] is None
