from __future__ import annotations

from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]


def test_r238_candidate_matches_r221_scoring() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_nonfin_quality_h5_sl20")
    control = engine.get("ai_hs300_sga_nonfin_quality_h20_sl20")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"] == control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None, "电子"],
        "ma60": [1.0, 1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_sga_ratio_4q_low": [-0.1, -0.2, -0.3, -0.4, None],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
