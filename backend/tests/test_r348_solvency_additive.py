from __future__ import annotations

from pathlib import Path

from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"


def test_r348_additive_solvency_leg_preserves_control_relative_weights() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_solvency_additive_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    scoring = strategy.meta["scoring"]
    assert scoring == {
        "momentum_60d": 0.34,
        "uf_quality_value_defensive": 0.2125,
        "uf_cash_quality": 0.17,
        "uf_low_vol": 0.1275,
        "uf_solvency_buffer": 0.15,
    }
    assert abs(sum(scoring.values()) - 1.0) < 1e-12
    control_weights = {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    }
    for factor, control_weight in control_weights.items():
        assert scoring[factor] == control_weight * 0.85
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
