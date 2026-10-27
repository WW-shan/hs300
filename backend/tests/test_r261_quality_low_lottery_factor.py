from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_r261_quality_low_lottery_factor.py"
BACKTEST = ROOT / "scripts" / "backtest_r261_quality_low_lottery.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r261_low_lottery_uses_20_trading_day_maximum_daily_return() -> None:
    module = _load(BUILDER, "build_r261_quality_low_lottery_price_window")
    days = [date(2024, 1, 2) + timedelta(days=index) for index in range(26)]
    closes = [10.0] * 5 + [12.0] + [12.0] * 20
    prices = pl.DataFrame({"date": days, "symbol": ["A"] * 26, "close": closes})

    factor = module._low_lottery_frame_from_prices(prices).sort("date")
    values = factor[module.LOW_LOTTERY_FIELD].to_list()

    assert values[:20] == [None] * 20
    assert math.isclose(float(values[20]), -0.2, abs_tol=1e-12)
    assert math.isclose(float(values[25]), 0.0, abs_tol=1e-12)


def test_r261_quality_low_lottery_min_rank_formula_and_missing() -> None:
    module = _load(BUILDER, "build_r261_quality_low_lottery_formula")
    panel = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 4 + [date(2024, 1, 3)] * 3,
            "symbol": ["A", "B", "C", "D", "A", "B", "C"],
            module.R257_FIELD: [0.9, 0.8, 0.2, None, 0.5, 0.5, 0.3],
            module.LOW_LOTTERY_FIELD: [
                -0.01,
                -0.20,
                -0.05,
                -0.10,
                -0.05,
                -0.10,
                -0.20,
            ],
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
        row["symbol"] + str(row["date"]): row[module.FIELD]
        for row in factor.iter_rows(named=True)
    }
    expected = {
        "A2024-01-02": 1.0,
        "B2024-01-02": 0.0,
        "C2024-01-02": 0.0,
        "D2024-01-02": None,
        "A2024-01-03": 0.75,
        "B2024-01-03": 0.5,
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


def test_r261_rank_excludes_nonmembers_and_requires_both_components() -> None:
    module = _load(BUILDER, "build_r261_quality_low_lottery_members")
    panel = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 4,
            "symbol": ["A", "B", "X", "C"],
            module.R257_FIELD: [0.9, 0.4, 9.9, 0.7],
            module.LOW_LOTTERY_FIELD: [-0.1, -0.2, -0.01, None],
        }
    )
    members = pl.DataFrame(
        {
            "effective_date": [date(2020, 1, 1)] * 3,
            "symbol": ["A", "B", "C"],
        }
    )
    factor = module._min_rank_factor(panel, members).sort("symbol")
    values = {
        row["symbol"]: row[module.FIELD] for row in factor.iter_rows(named=True)
    }
    assert math.isclose(float(values["A"]), 1.0, abs_tol=1e-12)
    assert math.isclose(float(values["B"]), 0.0, abs_tol=1e-12)
    assert values["C"] is None
    assert values["X"] is None

def test_r261_candidate_and_control_strategies_load_and_match_universe() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_qrs_low_lottery_h20_sl20")
    control = engine.get("ai_hs300_qrs_low_lottery_control")
    new_factor_id = "ext_alt_hs300_qlot_quality_low_lottery_min_rank"
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
            "industry_l1": [
                "\u7535\u5b50",
                "\u7535\u5b50",
                "\u7535\u5b50",
                "\u94f6\u884c",
                "\u7535\u5b50",
            ],
            "ma60": [1.0, 1.0, 1.0, 1.0, 1.0],
            new_factor_id: [0.8, None, 0.3, 0.9, 0.7],
        }
    )
    expected = ["\u7535\u5b50", "\u7535\u5b50", "\u7535\u5b50"]
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == expected
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == expected


def test_r261_backtest_contract_constants() -> None:
    module = _load(BACKTEST, "backtest_r261_quality_low_lottery")

    assert module.ROUND == "R261"
    assert module.FACTOR_ID == "ext_alt_hs300_qlot_quality_low_lottery_min_rank"
    assert module.FACTOR_COLUMN == "quality_low_lottery_min_rank"
    assert module.FACTOR_CONFIG == "alt_hs300_qlot"
    assert module.R257_FACTOR_ID == "ext_alt_hs300_qrs_quality_relative_strength_min_rank"
    assert module.R257_COLUMN == "quality_relative_strength_min_rank"
    assert module.LOTTERY_WINDOW == 20
    assert module.LOW_LOTTERY_COLUMN == "low_max_ret_20d"
    assert module.R249_FACTOR_ID == "ext_alt_hs300_skew_stock_skew_60d_low"
    assert module.COMPONENT_COLUMNS == (
        "quality_relative_strength_min_rank",
        "low_max_ret_20d",
    )
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_qrs_low_lottery_h20_sl20",
        "control": "ai_hs300_qrs_low_lottery_control",
    }
    assert module.OUT.name == "r261_quality_low_lottery.json"
    assert module.CACHE.name == "r261_cache"
    assert date(2023, 5, 4) == module.START
    assert [
        (date(2023, 5, 4), date(2024, 12, 31)),
        (date(2025, 1, 2), date(2026, 10, 16)),
    ] == module.SEGMENTS
    assert callable(module._independent_risk_audit)
