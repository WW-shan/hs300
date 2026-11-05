from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
AI_DIR = ROOT / "data" / "strategies" / "ai"
FACTOR_PATH = ROOT / "data" / "user_data" / "custom_factors" / "uf_intraday_risk_share.json"


def test_r352_intraday_risk_share_formula_is_bounded_and_backward_looking() -> None:
    definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
    compiled = compile_formula(definition["formula"])

    assert compiled.ok is True
    assert compiled.dependencies == {"open", "close", "prev_close"}
    rows = 25
    frame = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * rows,
            "date": list(range(rows)),
            "prev_close": [10.0 + (i % 3) * 0.1 for i in range(rows)],
            "open": [10.2 + (i % 5) * 0.05 for i in range(rows)],
            "close": [10.1 + (i % 7) * 0.03 for i in range(rows)],
        }
    )
    result = compiled.frame_transform(frame)
    assert result is not None
    values = result[FACTOR_COLUMN].to_list()
    assert values[-1] is not None
    assert 0.0 <= float(values[-1]) <= 1.0


def test_r352_intraday_risk_share_strategy_replaces_only_low_vol_and_neutralizes_it() -> None:
    engine = StrategyEngine(strategy_dirs=[AI_DIR])
    strategy = engine.get("ai_hs300_quality_momentum_intraday_risk_h60")

    assert strategy is not None
    assert strategy.meta["research_only"] is True
    assert strategy.max_hold_days == 60
    assert strategy.stop_loss is None
    assert strategy.meta["scoring"] == {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_intraday_risk_share": 0.15,
    }
    assert strategy.meta["neutralize"] == {
        "industry": "industry_l1",
        "styles": [],
        "exclude": ["momentum_60d", "uf_quality_value_defensive", "uf_cash_quality"],
        "shrink": 1.0,
    }
    assert strategy.filter_fn is not None
    frame = pl.DataFrame(
        {
            "ma60": [1.0, 1.0],
            "momentum_60d": [0.1, 0.1],
            "roe_latest": [10.0, 10.0],
            "pb_latest": [1.0, 1.0],
            "debt_ratio_latest": [40.0, 100.0],
            "ocf_ni": [1.0, 1.0],
            "accrual": [0.0, 0.0],
            "annual_vol_20d": [0.2, 0.2],
            "industry_l1": ["电子", "电子"],
        }
    )
    filtered = frame.filter(strategy.filter_fn(frame, {}))
    assert filtered.height == 1
    assert filtered["debt_ratio_latest"].to_list() == [40.0]
