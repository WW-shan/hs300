from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r558_ready_parameter_mc.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r558_ready_parameter_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r558_freezes_ready_strategy_parameter_grid() -> None:
    module = _runner()
    design = module.scientific_design()

    assert module.RESEARCH_ID == "R558"
    assert module.RESEARCH_TYPE == "project_formal"
    assert design["grid"] == {
        "entry_score": [60.0, 70.0, 80.0],
        "top_rank": [10, 20, 30],
        "max_industry_positions": [2, 3, 5],
    }
    assert design["fixed"]["max_positions"] == 10
    assert design["fixed"]["max_hold_days"] == 60
    assert design["fixed"]["stop_loss"] == -0.12


def test_r558_declares_train_select_validation_confirm_and_gbm() -> None:
    module = _runner()
    standard = module.scientific_design()["standard_validation"]

    assert standard["parameter_mc"]["selection_policy"] == "train_select_validation_confirm"
    assert standard["parameter_mc"]["required"] is True
    assert standard["gbm"] == {"required": True, "path_count": 100}


def test_r558_grid_is_unique_and_complete() -> None:
    module = _runner()
    points = module.parameter_grid()

    assert len(points) == 27
    keys = {
        (row["params"]["entry_score"], row["params"]["top_rank"], row["params"]["max_industry_positions"])
        for row in points
    }
    assert len(keys) == 27
