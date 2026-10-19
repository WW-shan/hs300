from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_r221_candidate_and_control_load_with_nonfin_filter() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_nonfin_quality_h20_sl20")
    control = engine.get("ai_hs300_sga_nonfin_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_sga_ratio_4q_low"] == 0.45
    assert "ext_alt_hs300_sga_ratio_4q_low" not in control.meta["scoring"]
    assert "industry_l1" in candidate.required_features
    assert "industry_l1" in control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_sga_ratio_4q_low": [-0.1, -0.2, -0.3, -0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]


def test_engine_attaches_required_industry_and_fails_closed(tmp_path: Path) -> None:
    from app.strategy.engine import StrategyDataContext, StrategyEngine

    strategy_path = tmp_path / "industry_filter_test.py"
    strategy_path.write_text(
        '''
import polars as pl

META = {
    "id": "industry_filter_test",
    "name": "industry filter test",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "params": [],
    "scoring": {"close": 1.0},
    "limit": 10,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
REQUIRED_FEATURES = {"industry_l1"}

def filter(df, params):
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
    )
''',
        encoding="utf-8",
    )
    current = pl.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "close": [10.0, 20.0, 30.0, 40.0],
    })
    mapping = pl.DataFrame({
        "symbol": ["A", "B", "C", "D"],
        "industry_l1": ["银行", "非银金融", "电子", None],
    })
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=date(2026, 10, 16),
        current=current,
    )

    engine = StrategyEngine(
        strategy_dirs=[tmp_path],
        industry_loader=lambda: mapping,
    )
    result = engine.run("industry_filter_test", context)
    assert [row["symbol"] for row in result.rows] == ["C"]

    no_loader = StrategyEngine(strategy_dirs=[tmp_path])
    with pytest.raises(ValueError, match="industry_loader"):
        no_loader.run("industry_filter_test", context)