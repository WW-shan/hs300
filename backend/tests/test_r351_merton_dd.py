from __future__ import annotations

import json
import math
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"
FACTOR_PATH = ROOT / "data" / "user_data" / "custom_factors" / "uf_merton_dd.json"


def test_r351_merton_dd_formula_is_structural_and_backward_looking() -> None:
    definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
    compiled = compile_formula(definition["formula"])

    assert compiled.ok is True
    assert compiled.dependencies == {"pb_latest", "debt_ratio_latest", "annual_vol_20d"}
    frame = pl.DataFrame(
        {
            "pb_latest": [1.0, 2.0, 3.0, None],
            "debt_ratio_latest": [40.0, 40.0, 80.0, 50.0],
            "annual_vol_20d": [0.20, 0.20, 0.40, 0.20],
        }
    )
    result = compiled.frame_transform(frame)
    assert result is not None
    values = result[FACTOR_COLUMN].to_list()
    q0 = (1.0 - 0.40) / 0.40
    q1 = 2.0 * (1.0 - 0.40) / 0.40
    q2 = 3.0 * (1.0 - 0.80) / 0.80
    assert math.isclose(values[0], math.log(1 + q0) * (1 + q0) / (0.20 * q0) - 0.5 * 0.20 * q0 / (1 + q0), rel_tol=1e-12)
    assert math.isclose(values[1], math.log(1 + q1) * (1 + q1) / (0.20 * q1) - 0.5 * 0.20 * q1 / (1 + q1), rel_tol=1e-12)
    assert math.isclose(values[2], math.log(1 + q2) * (1 + q2) / (0.40 * q2) - 0.5 * 0.40 * q2 / (1 + q2), rel_tol=1e-12)
    assert values[3] is None


def test_r351_merton_dd_strategy_replaces_only_low_vol_and_neutralizes_it() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_merton_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_merton_dd": 0.15,
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
            "industry_l1": ["电子", "电子"],
        }
    )
    filtered = frame.filter(strategy.filter_fn(frame, {}))
    assert filtered.height == 1
    assert filtered["debt_ratio_latest"].to_list() == [40.0]
