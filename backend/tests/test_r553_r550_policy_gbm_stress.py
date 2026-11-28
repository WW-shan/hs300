from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r553_r550_policy_gbm_stress.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r553_r550_policy_gbm_stress", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r553_declares_policy_gbm_contract() -> None:
    module = _runner()
    assert module.RESEARCH_ID == "R553"
    assert module.RESEARCH_TYPE == "synthetic_market_stress"
    assert module.POLICY == "vol_12"
    assert module.scientific_design()["standard_validation"]["gbm"]["path_count"] == 100


def test_r553_policy_path_reports_leverage_and_benchmark() -> None:
    module = _runner()
    from app.backtest.synthetic_gbm import SyntheticPathSpec

    scores = np.zeros((40, 24), dtype=np.float32)
    scores[:, 0] = 100.0
    scores[:, 1:] = 45.0
    result = module.run_policy_path(
        SyntheticPathSpec("neutral", 0.0, 0),
        scores=scores,
        asset_count=24,
        warmup_days=20,
        evaluation_days=40,
        max_positions=5,
        max_hold_days=20,
    )
    row = result["observations"][0]
    assert row["strategy_max_exposure"] <= 1.0
    assert row["benchmark_max_exposure"] >= 0.95
    assert result["realized"]["close_sha256"]
