from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r555_neutralization_shrink_mc.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r555_neutralization_shrink_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r555_freezes_shrink_grid_and_project_formal_contract() -> None:
    module = _runner()
    design = module.scientific_design()

    assert module.RESEARCH_ID == "R555B"
    assert module.RESEARCH_TYPE == "project_formal"
    assert design["grid"]["shrink"] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert design["selection"]["policy"] == "train_select_validation_confirm"
    assert design["selection"]["recent_used_for_selection"] is False
    assert design["standard_validation"]["gbm"]["path_count"] == 100
    assert design["standard_validation"]["parameter_mc"]["required"] is True


def test_r555_zero_shrink_is_raw_control_and_positive_shrink_is_neutralized() -> None:
    module = _runner()

    assert module.neutralization(0.0) is None
    assert module.neutralization(0.5) == {
        "industry": "industry_l1",
        "styles": [],
        "shrink": 0.5,
    }


def test_r555_parameter_grid_is_unique_and_centered() -> None:
    module = _runner()
    points = module.parameter_grid()

    assert len(points) == 5
    assert [row["params"]["shrink"] for row in points] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert module.CENTER_SHRINK == 0.5
