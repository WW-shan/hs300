from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_r259_quality_rs_pullback_factor.py"
BACKTEST = ROOT / "scripts" / "backtest_r259_quality_rs_pullback.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r259_reversal_uses_five_trading_day_price_change() -> None:
    module = _load(BUILDER, "build_r259_quality_rs_pullback_factor_reversal")
    prices = pl.DataFrame(
        {
            "date": [
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
                date(2024, 1, 5),
                date(2024, 1, 8),
                date(2024, 1, 9),
                date(2024, 1, 10),
            ],
            "symbol": ["A"] * 7,
            "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
        }
    )
    factor = module._reversal_frame_from_prices(prices).sort("date")
    values = factor[module.REVERSAL_FIELD].to_list()
    assert values[:5] == [None] * 5
    assert math.isclose(float(values[5]), -0.5, abs_tol=1e-12)
    assert math.isclose(float(values[6]), -(16.0 / 11.0 - 1.0), abs_tol=1e-12)


def test_r259_quality_rs_pullback_min_rank_formula_and_missing() -> None:
    module = _load(BUILDER, "build_r259_quality_rs_pullback_factor_formula")
    panel = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 4 + [date(2024, 1, 3)] * 3,
            "symbol": ["A", "B", "C", "D", "A", "B", "C"],
            module.R257_FIELD: [0.9, 0.8, 0.2, None, 0.5, 0.5, 0.3],
            module.REVERSAL_FIELD: [0.8, 0.2, 0.9, 0.1, 0.5, 0.5, 0.8],
        }
    )
    members = pl.DataFrame(
        {
            "effective_date": [date(2020, 1, 1)] * 4,
            "symbol": ["A", "B", "C", "D"],
        }
    )
    factor = module._min_rank_factor(panel, members).sort(["date", "symbol"])
    values = {
        row["symbol"] + str(row["date"]): row[module.FIELD] for row in factor.iter_rows(named=True)
    }
    expected = {
        "A2024-01-02": 0.5,
        "B2024-01-02": 0.0,
        "C2024-01-02": 0.0,
        "D2024-01-02": None,
        "A2024-01-03": 0.25,
        "B2024-01-03": 0.25,
        "C2024-01-03": 0.0,
    }
    for key, target in expected.items():
        actual = values[key]
        if target is None:
            assert actual is None
        else:
            assert math.isclose(float(actual), target, abs_tol=1e-12)
    valid = factor.filter(pl.col(module.FIELD).is_not_null())[module.FIELD]
    assert valid.min() == 0.0
    assert valid.max() <= 1.0


def test_r259_candidate_and_control_strategies_load_and_match_universe() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_qrs_pullback_h20_sl20")
    control = engine.get("ai_hs300_qrs_pullback_control")
    new_factor_id = "ext_alt_hs300_qrspb_quality_rs_pullback_min_rank"
    r257_factor_id = "ext_alt_hs300_qrs_quality_relative_strength_min_rank"

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"][new_factor_id] == 0.45
    assert r257_factor_id not in candidate.meta["scoring"]
    assert control.meta["scoring"][r257_factor_id] == 0.45
    assert new_factor_id not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert new_factor_id in candidate.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)
    assert math.isclose(sum(control.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame(
        {
            "industry_l1": ["电子", "电子", "电子", "银行", "电子"],
            "ma60": [1.0, 1.0, 1.0, 1.0, 1.0],
            new_factor_id: [0.8, None, 0.3, 0.9, 0.7],
        }
    )
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == [
        "电子",
        "电子",
        "电子",
    ]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == [
        "电子",
        "电子",
        "电子",
    ]


def test_r259_backtest_contract_constants() -> None:
    module = _load(BACKTEST, "backtest_r259_quality_rs_pullback")

    assert module.ROUND == "R259"
    assert module.FACTOR_ID == "ext_alt_hs300_qrspb_quality_rs_pullback_min_rank"
    assert module.FACTOR_COLUMN == "quality_rs_pullback_min_rank"
    assert module.R257_FACTOR_ID == "ext_alt_hs300_qrs_quality_relative_strength_min_rank"
    assert module.R257_COLUMN == "quality_relative_strength_min_rank"
    assert module.REVERSAL_FIELD == "reversal_5d"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_qrs_pullback_h20_sl20",
        "control": "ai_hs300_qrs_pullback_control",
    }
    assert module.OUT.name == "r259_quality_rs_pullback.json"
    assert module.CACHE.name == "r259_cache"
    assert date(2023, 5, 4) == module.START
    assert [
        (date(2023, 5, 4), date(2024, 12, 31)),
        (date(2025, 1, 2), date(2026, 10, 16)),
    ] == module.SEGMENTS
