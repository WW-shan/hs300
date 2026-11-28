from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r552_cash_safety_parameter_mc.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r552c_cash_safety_parameter_mc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r552_declares_project_formal_and_standard_suite() -> None:
    module = _runner()
    assert module.RESEARCH_ID == "R552C"
    assert module.RESEARCH_TYPE == "statistical_screen"
    design = module.scientific_design()
    assert design["grid_size"] == 27
    assert design["grid"]["positions"] == [8, 10, 12]
    assert design["grid"]["hold_days"] == [40, 60, 80]
    assert design["grid"]["vol_targets"] == [0.10, 0.12, 0.14]
    assert design["standard_validation"]["gbm"]["required"] is False
    assert design["standard_validation"]["parameter_mc"]["required"] is True


def test_r552_parameter_grid_is_unique_and_complete() -> None:
    module = _runner()
    points = module.parameter_grid()
    assert len(points) == 27
    keys = {(row["positions"], row["hold_days"], row["vol_target"]) for row in points}
    assert len(keys) == 27
    assert all(row["stop_loss"] == -0.12 for row in points)
