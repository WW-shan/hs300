from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r551_r550_gbm_stress.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r551a_r550_gbm_stress", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r551_declares_synthetic_standard_validation() -> None:
    module = _runner()
    assert module.RESEARCH_ID == "R551A"
    assert module.RESEARCH_TYPE == "synthetic_market_stress"
    assert module.RESEARCH_PREREGISTRATION.endswith(
        "r551a_r550_gbm_stress_preregistration.json"
    )
    design = module.scientific_design()
    assert design["standard_validation"]["gbm"]["path_count"] == 100
    assert design["standard_validation"]["parameter_mc"]["status"] == "fixed_frozen"


def test_r551_score_matrix_freezes_dates_symbols_and_direction() -> None:
    module = _runner()
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)] * 3,
            "symbol": ["a", "b", "c", "a", "b", "c"],
            "score": [10.0, 80.0, None, 20.0, 90.0, 30.0],
        }
    )
    matrix, symbols, dates = module.build_frozen_score_matrix(frame, target_dates=[date(2024,1,2), date(2024,1,3)], target_symbols=["a", "b", "c"])
    assert symbols == ("a", "b", "c")
    assert dates == (date(2024,1,2), date(2024,1,3))
    assert matrix.shape == (2, 3)
    assert matrix[0, 0] == 10.0
    assert matrix[0, 2] == 0.0
    assert matrix[1, 2] == 30.0
