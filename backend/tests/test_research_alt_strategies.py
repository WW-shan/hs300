from __future__ import annotations

import json
from pathlib import Path

from app.strategy.engine import StrategyEngine

AI_DIR = Path(__file__).resolve().parents[2] / "data" / "strategies" / "ai"
CUSTOM_FACTOR_DIR = Path(__file__).resolve().parents[2] / "data" / "user_data" / "custom_factors"


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


def test_r102_composite_factors_are_declared() -> None:
    expected = {
        "cf_industry_cash_quality": {
            "ext_alt_hs300_stock_rel_industry_20d": 0.40,
            "uf_quality_value_defensive": 0.30,
            "uf_cash_quality": 0.30,
        },
        "cf_industry_lowrisk_quality": {
            "ext_alt_hs300_stock_rel_industry_20d": 0.45,
            "uf_low_vol": 0.25,
            "uf_quality_value_defensive": 0.30,
        },
        "cf_industry_fund_reduction": {
            "ext_alt_hs300_stock_rel_industry_20d": 0.35,
            "ext_alt_hs300_fund_count_chg_qoq_low": 0.20,
            "ext_alt_hs300_fund_holding_ratio_chg_qoq_low": 0.20,
            "uf_cash_quality": 0.15,
            "uf_quality_value_defensive": 0.10,
        },
    }
    for factor_id, members in expected.items():
        definition = json.loads((CUSTOM_FACTOR_DIR / f"{factor_id}.json").read_text())
        assert definition["id"] == factor_id
        assert definition["kind"] == "composite"
        assert definition["members"] == members


def test_r102_composite_strategies_load() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    expected = {
        "ai_hs300_industry_cash_quality": "cf_industry_cash_quality",
        "ai_hs300_industry_cash_quality_industry_neutral": "cf_industry_cash_quality",
        "ai_hs300_industry_cash_macro_gate": "cf_industry_cash_quality",
        "ai_hs300_industry_cash_breadth_gate": "cf_industry_lowrisk_quality",
        "ai_hs300_industry_fund_reduction_combo": "cf_industry_fund_reduction",
    }
    for strategy_id, factor in expected.items():
        strategy = engine.get(strategy_id)
        assert strategy is not None
        assert factor in strategy.meta["scoring"]
    neutral = engine.get("ai_hs300_industry_cash_quality_industry_neutral")
    assert neutral is not None
    assert neutral.meta["neutralize"] == {"industry": "industry_l1", "shrink": 0.5}


def test_r102c_optimized_hold_strategy_loads() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_industry_relative_strength_quality_h20")
    assert strategy is not None
    assert strategy.max_hold_days == 20
    assert strategy.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }


def test_r102d_stop_loss_strategy_loads() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_industry_relative_strength_quality_h20_sl20")
    assert strategy is not None
    assert strategy.max_hold_days == 20
    assert strategy.stop_loss == -0.20


def test_r103_breadth_gate_strategy_loads() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_industry_relative_strength_quality_h20_sl20_breadth45")
    assert strategy is not None
    assert strategy.max_hold_days == 20
    assert strategy.stop_loss == -0.20


def test_r104_stock_sentiment_factors_are_declared() -> None:
    expected = {
        "cf_reversal_sentiment": {
            "momentum_5d": -0.30,
            "momentum_10d": -0.15,
            "rsi_14_delta_5d": -0.15,
            "close_position": 0.20,
            "turnover_z_60d": -0.10,
            "max_ret_20d": -0.10,
        },
        "cf_volume_price_structure": {
            "vol_price_corr_20d": 0.25,
            "obv_trend_20d": 0.25,
            "amount_ratio_5d": 0.15,
            "vol_trend_5_10": 0.15,
            "close_position": 0.20,
        },
        "cf_tail_risk_low": {
            "downside_vol_20d": -0.30,
            "annual_vol_20d": -0.25,
            "ret_skew_20d": 0.20,
            "max_ret_20d": -0.15,
            "vol_regime_5_60": -0.10,
        },
    }
    for factor_id, members in expected.items():
        definition = json.loads((CUSTOM_FACTOR_DIR / f"{factor_id}.json").read_text())
        assert definition["id"] == factor_id
        assert definition["kind"] == "composite"
        assert definition["members"] == members


def test_r104_stock_sentiment_strategies_load() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    expected = {
        "ai_hs300_reversal_sentiment_quality": "cf_reversal_sentiment",
        "ai_hs300_volume_price_quality": "cf_volume_price_structure",
        "ai_hs300_tail_risk_quality": "cf_tail_risk_low",
        "ai_hs300_sentiment_tail_combo": "cf_reversal_sentiment",
    }
    for strategy_id, factor in expected.items():
        strategy = engine.get(strategy_id)
        assert strategy is not None
        assert factor in strategy.meta["scoring"]
        assert strategy.max_hold_days == 20
        assert strategy.stop_loss == -0.20


def test_r105_unreferenced_family_factors_are_declared() -> None:
    expected = {
        "cf_trend_confirmation": {
            "ma20_bias": 0.20,
            "ma60_bias": 0.20,
            "macd_hist_pct": 0.20,
            "kdj_kd_diff": 0.15,
            "distance_to_high_60d": 0.15,
            "vol_trend_5_60": 0.10,
        },
        "cf_price_position": {
            "position_240d": 0.25,
            "distance_to_high_240d": 0.25,
            "distance_from_low_60d": 0.15,
            "boll_position": 0.20,
            "vwap_bias": 0.15,
        },
        "cf_liquidity_structure": {
            "amount_mean_20d": -0.25,
            "turnover_mean_20d": -0.20,
            "turnover_std_20d": -0.20,
            "amihud_20d": 0.20,
            "log_amount": -0.15,
        },
    }
    for factor_id, members in expected.items():
        definition = json.loads((CUSTOM_FACTOR_DIR / f"{factor_id}.json").read_text())
        assert definition["id"] == factor_id
        assert definition["kind"] == "composite"
        assert definition["members"] == members


def test_r105_unreferenced_family_strategies_load() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    expected = {
        "ai_hs300_trend_confirmation_quality": "cf_trend_confirmation",
        "ai_hs300_price_position_quality": "cf_price_position",
        "ai_hs300_liquidity_structure_quality": "cf_liquidity_structure",
    }
    for strategy_id, factor in expected.items():
        strategy = engine.get(strategy_id)
        assert strategy is not None
        assert factor in strategy.meta["scoring"]
        assert strategy.max_hold_days == 20
        assert strategy.stop_loss == -0.20
