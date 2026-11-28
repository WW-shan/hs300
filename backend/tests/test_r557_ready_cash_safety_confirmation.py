from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r557_ready_cash_safety_confirmation.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r557_ready_cash_safety_confirmation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r557_freezes_ui_selectable_strategy_and_policy_contract() -> None:
    module = _runner()

    assert module.RESEARCH_ID == "R557C"
    assert module.RESEARCH_TYPE == "project_formal"
    assert module.STRATEGY_ID == "ai_hs300_cash_safety_v14"
    assert module.POLICY_ID == "up_vol_target_14"
    assert module.FACTOR_ID == "uf_r550_cash_safety_rank"
    assert module.FIXED_PARAMETERS == {
        "max_positions": 10,
        "max_hold_days": 60,
        "stop_loss": -0.12,
        "entry_score": 70.0,
        "exit_score": 40.0,
        "top_rank": 20,
        "max_industry_positions": 3,
    }


def test_r557_declares_full_gbm_and_frozen_parameter_receipt() -> None:
    module = _runner()
    standard = module.scientific_design()["standard_validation"]

    assert standard["gbm"] == {"required": True, "path_count": 100}
    assert standard["parameter_mc"]["status"] == "fixed_frozen"
    assert standard["parameter_mc"]["source_research_id"] == "R552C"


def test_r557_ready_strategy_gbm_path_uses_policy_and_industry_cap() -> None:
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
        cost_multipliers=(2,),
    )

    row = result["observations"][0]
    assert row["strategy_max_exposure"] <= 1.0
    assert row["benchmark_max_exposure"] >= 0.95
    assert row["policy"] == "up_vol_target_14"
