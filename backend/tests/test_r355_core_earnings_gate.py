from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"
FACTOR_PATH = ROOT / "data" / "user_data" / "custom_factors" / "uf_core_earnings_quality.json"


def test_r355_core_earnings_factor_tracks_ttm_operating_profit_share() -> None:
    definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
    compiled = compile_formula(definition["formula"])

    assert compiled.ok is True
    assert compiled.dependencies == {"core_earnings_ratio"}
    frame = pl.DataFrame({"core_earnings_ratio": [0.25, 0.75, None]})
    result = compiled.frame_transform(frame)
    assert result is not None
    assert result[FACTOR_COLUMN].to_list() == [0.25, 0.75, None]


def test_r355_core_earnings_gate_preserves_a_sleeve_score_and_rejects_noncore_profit() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_macro_gate_quality_core_earnings_gate")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 35
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
        "uf_cash_quality": 0.20,
    }
    frame = pl.DataFrame(
        {
            "ma60": [1.0, 1.0, 1.0],
            "ext_alt_hs300_holder_top10_free_float_share": [10.0, 10.0, 10.0],
            "ext_alt_hs300_equity_risk_premium": [6.0, 6.0, 6.0],
            "ext_alt_hs300_credit_spread_5y_low": [0.0, 0.0, 0.0],
            "ext_alt_hs300_term_spread_10y_2y": [0.5, 0.5, 0.5],
            "core_earnings_ratio": [0.75, 0.49, None],
        }
    )
    filtered = frame.filter(strategy.filter_fn(frame, {}))
    assert filtered.height == 1
    assert filtered["core_earnings_ratio"].to_list() == [0.75]
