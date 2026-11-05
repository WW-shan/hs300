from __future__ import annotations

from pathlib import Path

import polars as pl

from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"


def test_r358_b_core_earnings_gate_preserves_b_score_and_rejects_noncore_profit() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_industry_relative_strength_quality_core_earnings_gate")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 20
    assert strategy.stop_loss == -0.20
    assert strategy.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }
    frame = pl.DataFrame(
        {
            "ma60": [1.0, 1.0, 1.0],
            "ext_alt_hs300_stock_rel_industry_20d": [0.1, 0.1, 0.1],
            "core_earnings_ratio": [0.75, 0.49, None],
        }
    )
    filtered = frame.filter(strategy.filter_fn(frame, {}))
    assert filtered.height == 1
    assert filtered["core_earnings_ratio"].to_list() == [0.75]
