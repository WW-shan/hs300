from __future__ import annotations

from pathlib import Path

import polars as pl

from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"


def test_r354_agreement_gate_keeps_only_two_of_four_top_ranks() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_agreement_gate_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    }
    frame = pl.DataFrame(
        {
            "date": [1] * 5,
            "symbol": ["A", "B", "C", "D", "E"],
            "ma60": [1.0] * 5,
            "momentum_60d": [4.0, 1.0, 1.0, 1.0, 5.0],
            "roe_latest": [0.0, 50.0, 0.0, 0.0, 60.0],
            "pb_latest": [1.0] * 5,
            "debt_ratio_latest": [50.0, 50.0, 50.0, 50.0, 10.0],
            "ocf_ni": [0.0, 0.0, 10.0, 0.0, 12.0],
            "accrual": [0.0] * 5,
            "annual_vol_20d": [0.5, 0.4, 0.3, 0.2, 0.1],
            "industry_l1": ["电子"] * 5,
        }
    )
    filtered = frame.filter(strategy.filter_fn(frame, {}))
    assert filtered["symbol"].to_list() == ["E"]
