from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"
FACTOR_PATH = ROOT / "data" / "user_data" / "custom_factors" / "uf_factor_agreement.json"


def test_r353_factor_agreement_counts_top_ranked_components() -> None:
    definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
    compiled = compile_formula(definition["formula"])

    assert compiled.ok is True
    assert compiled.dependencies == {
        "momentum_60d",
        "roe_latest",
        "pb_latest",
        "debt_ratio_latest",
        "ocf_ni",
        "accrual",
        "annual_vol_20d",
    }
    frame = pl.DataFrame(
        {
            "date": [1] * 5,
            "symbol": ["A", "B", "C", "D", "E"],
            "momentum_60d": [4.0, 1.0, 1.0, 1.0, 5.0],
            "roe_latest": [0.0, 50.0, 0.0, 0.0, 60.0],
            "pb_latest": [1.0, 1.0, 1.0, 1.0, 1.0],
            "debt_ratio_latest": [50.0, 50.0, 50.0, 50.0, 10.0],
            "ocf_ni": [0.0, 0.0, 10.0, 0.0, 12.0],
            "accrual": [0.0, 0.0, 0.0, 0.0, 0.0],
            "annual_vol_20d": [0.5, 0.4, 0.3, 0.2, 0.1],
        }
    )
    result = compiled.frame_transform(frame)
    assert result is not None
    assert result.sort("symbol")[FACTOR_COLUMN].to_list() == [1, 1, 1, 1, 4]


def test_r353_factor_agreement_is_added_as_a_fifth_c_leg() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_agreement_additive_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "momentum_60d": 0.34,
        "uf_quality_value_defensive": 0.2125,
        "uf_cash_quality": 0.17,
        "uf_low_vol": 0.1275,
        "uf_factor_agreement": 0.15,
    }
    assert strategy.meta["neutralize"] == {
        "industry": "industry_l1",
        "styles": [],
        "exclude": [
            "momentum_60d",
            "uf_quality_value_defensive",
            "uf_cash_quality",
            "uf_low_vol",
        ],
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
