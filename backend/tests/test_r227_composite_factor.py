from __future__ import annotations

from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]


def test_r227_composite_strategies_load_and_share_factor_filter() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_gm_trend_composite_h20_sl20")
    control = engine.get("ai_hs300_sga_gm_trend_composite_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_sga_ratio_4q_low": 0.225,
        "ext_alt_hs300_gross_margin_yoy_improvement_q": 0.225,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
    }
    assert control.meta["scoring"] == {
        "uf_quality_value_defensive": 0.545,
        "uf_cash_quality": 0.455,
    }
    assert {
        "ext_alt_hs300_sga_ratio_4q_low",
        "ext_alt_hs300_gross_margin_yoy_improvement_q",
        "industry_l1",
    } <= candidate.required_features
    assert {
        "ext_alt_hs300_sga_ratio_4q_low",
        "ext_alt_hs300_gross_margin_yoy_improvement_q",
        "industry_l1",
    } <= control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None, "医药生物"],
        "ma60": [1.0, 1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_sga_ratio_4q_low": [-0.1, -0.2, -0.3, -0.4, None],
        "ext_alt_hs300_gross_margin_yoy_improvement_q": [-0.01, -0.02, -0.03, -0.04, -0.05],
    })
    expected = ["电子"]
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == expected
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == expected