from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"
FACTOR_PATH = ROOT / "data" / "user_data" / "custom_factors" / "uf_solvency_buffer.json"


def test_r347_solvency_factor_formula_and_dependencies() -> None:
    definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
    compiled = compile_formula(definition["formula"])

    assert compiled.ok is True
    assert compiled.dependencies == {"debt_ratio_latest", "annual_vol_20d"}
    frame = pl.DataFrame(
        {
            "debt_ratio_latest": [20.0, 80.0, None],
            "annual_vol_20d": [0.20, 0.40, 0.30],
        }
    )
    result = compiled.frame_transform(frame)
    assert result is not None
    values = result[FACTOR_COLUMN].to_list()
    assert math.isclose(values[0], math.log(0.8 / 0.20), rel_tol=1e-12)
    assert math.isclose(values[1], math.log(0.2 / 0.40), rel_tol=1e-12)
    assert values[2] is None


def test_r347_strategy_changes_only_c_low_vol_leg_and_neutralizes_it() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_solvency_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_solvency_buffer": 0.15,
    }
    assert strategy.meta["neutralize"] == {
        "industry": "industry_l1",
        "styles": [],
        "exclude": ["momentum_60d", "uf_quality_value_defensive", "uf_cash_quality"],
        "shrink": 1.0,
    }
    assert strategy.filter_fn is not None
    frame = pl.DataFrame(
        {
            "ma60": [1.0, 1.0],
            "momentum_60d": [0.1, 0.1],
            "roe_latest": [10.0, 10.0],
            "pb_latest": [1.0, 1.0],
            "debt_ratio_latest": [40.0, 100.0],
            "ocf_ni": [1.0, 1.0],
            "accrual": [0.0, 0.0],
            "annual_vol_20d": [0.2, 0.2],
            "uf_solvency_buffer": [0.5, -20.0],
            "industry_l1": ["电子", "电子"],
        }
    )
    assert frame.filter(strategy.filter_fn(frame, {}))["uf_solvency_buffer"].to_list() == [0.5]
