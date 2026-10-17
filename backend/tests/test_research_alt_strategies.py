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


def test_r100_event_strategies_load() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    expected = {
        "ai_hs300_lhb_quality": "ext_alt_hs300_lhb_net_buy_ratio_20d",
        "ai_hs300_pledge_quality": "ext_alt_hs300_pledge_ratio_low",
        "ai_hs300_pledge_quality_industry_neutral": "ext_alt_hs300_pledge_ratio_low",
        "ai_hs300_repurchase_quality": "ext_alt_hs300_repurchase_event_60d",
        "ai_hs300_block_trade_quality": "ext_alt_hs300_block_trade_ratio_20d_low",
        "ai_hs300_block_trade_quality_industry_neutral": "ext_alt_hs300_block_trade_ratio_20d_low",
        "ai_hs300_analyst_quality": "ext_alt_hs300_analyst_rating_score_20d",
        "ai_hs300_earnings_surprise_quality": "ext_alt_hs300_forecast_surprise_60d",
        "ai_hs300_event_combo": "ext_alt_hs300_repurchase_event_60d",
        "ai_hs300_event_combo_industry_neutral": "ext_alt_hs300_repurchase_event_60d",
    }

    for strategy_id, factor in expected.items():
        strategy = engine.get(strategy_id)
        assert strategy is not None
        assert factor in strategy.meta["scoring"]
        if strategy_id.endswith("_industry_neutral"):
            assert strategy.meta["neutralize"] == {"industry": "industry_l1", "shrink": 1.0}


def test_r101_industry_market_strategies_load() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    expected = {
        "ai_hs300_industry_momentum_quality": "ext_alt_hs300_industry_mom_20d",
        "ai_hs300_industry_relative_strength_quality": "ext_alt_hs300_stock_rel_industry_20d",
        "ai_hs300_mainline_quality": "ext_alt_hs300_mainline_top1_flag",
        "ai_hs300_macro_liquidity_quality": "ext_alt_hs300_m1_m2_scissors_chg_1m",
        "ai_hs300_risk_appetite_quality": "ext_alt_hs300_etf_share_chg_1m",
        "ai_hs300_industry_macro_combo": "ext_alt_hs300_industry_rps_20d",
    }
    for strategy_id, factor in expected.items():
        strategy = engine.get(strategy_id)
        assert strategy is not None
        assert factor in strategy.meta["scoring"]
