from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CASES = {
    "ai_hs300_qrs_tp20_h20_sl20": (0.20, 20),
    "ai_hs300_qrs_tp30_h20_sl20": (0.30, 20),
    "ai_hs300_qrs_tp40_h20_sl20": (0.40, 20),
    "ai_hs300_qrs_h10_sl20": (None, 10),
}


def test_r262_variants_load_expected_exit_parameters() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])

    for sid, (take_profit, max_hold_days) in CASES.items():
        strategy = engine.get(sid)
        assert strategy.meta["research_only"] is True
        assert strategy.stop_loss == -0.20
        assert strategy.take_profit == take_profit
        assert strategy.max_hold_days == max_hold_days
        assert strategy.required_features == engine.get(
            "ai_hs300_quality_relative_strength_h20_sl20"
        ).required_features
