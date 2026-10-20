from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_r240_candidate_differs_only_by_holding_period() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_nonfin_quality_h60_sl20")
    control = engine.get("ai_hs300_sga_nonfin_quality_h20_sl20")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"] == control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert candidate.max_hold_days == 60
    assert control.max_hold_days == 20
