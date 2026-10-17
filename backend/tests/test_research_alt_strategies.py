from __future__ import annotations

from pathlib import Path

from app.strategy.engine import StrategyEngine

AI_DIR = Path(__file__).resolve().parents[2] / "data" / "strategies" / "ai"


def test_r98_research_strategies_load_with_expected_neutralization() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])

    crowded = engine.get("ai_hs300_low_fund_crowding_quality")
    crowded_neutral = engine.get("ai_hs300_low_fund_crowding_quality_industry_neutral")
    reducing = engine.get("ai_hs300_fund_reduction_quality")
    reducing_neutral = engine.get("ai_hs300_fund_reduction_quality_industry_neutral")
    neutral = engine.get("ai_hs300_macro_gate_quality_industry_neutral")

    assert crowded is not None
    assert "ext_alt_hs300_fund_count_low" in crowded.meta["scoring"]
    assert crowded_neutral is not None
    assert crowded_neutral.meta["neutralize"] == {"industry": "industry_l1", "shrink": 1.0}
    assert reducing is not None
    assert "ext_alt_hs300_fund_count_chg_qoq_low" in reducing.meta["scoring"]
    assert reducing_neutral is not None
    assert reducing_neutral.meta["neutralize"] == {"industry": "industry_l1", "shrink": 1.0}
    assert neutral is not None
    assert neutral.meta["neutralize"] == {"industry": "industry_l1", "shrink": 1.0}
