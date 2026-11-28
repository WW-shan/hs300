from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r554_industry_neutral_confirmation.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r554_industry_neutral_confirmation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r554_freezes_project_formal_industry_neutral_variant() -> None:
    module = _runner()

    assert module.RESEARCH_ID == "R554"
    assert module.RESEARCH_TYPE == "project_formal"
    assert module.PRIMARY_VARIANT == "industry_neutral"
    assert module.scientific_design()["fixed_parameters"] == {
        "max_positions": 10,
        "max_hold_days": 60,
        "vol_target": 0.14,
        "stop_loss": -0.12,
        "industry_cap": 3,
    }


def test_r554_variant_contract_contains_raw_control_and_industry_neutral() -> None:
    module = _runner()
    variants = module.variants()

    assert tuple(variants) == ("control", "industry_neutral")
    assert variants["control"] is None
    assert variants["industry_neutral"] == {
        "industry": "industry_l1",
        "styles": [],
        "shrink": 1.0,
    }


def test_r554_declares_full_gbm_and_fixed_parameter_receipts() -> None:
    module = _runner()
    standard = module.scientific_design()["standard_validation"]

    assert standard["gbm"] == {"required": True, "path_count": 100}
    assert standard["parameter_mc"]["status"] == "fixed_frozen"
    assert standard["parameter_mc"]["source_research_id"] == "R552C"


def test_r554_synthetic_policy_supports_explicit_industry_neutralization() -> None:
    module = _runner()
    from app.backtest.synthetic_gbm import SyntheticPathSpec

    scores = np.zeros((40, 24), dtype=np.float32)
    scores[:, 0] = 100.0
    scores[:, 1:] = 45.0
    result = module.run_policy_path(
        SyntheticPathSpec("neutral", 0.0, 0),
        scores=scores,
        neutralize=module.variants()["industry_neutral"],
        asset_count=24,
        warmup_days=20,
        evaluation_days=40,
        max_positions=5,
        max_hold_days=20,
        cost_multipliers=(2,),
    )

    assert result["observations"][0]["strategy_max_exposure"] <= 1.0
    assert result["observations"][0]["benchmark_max_exposure"] >= 0.95
