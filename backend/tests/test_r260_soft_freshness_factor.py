from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_r260_soft_freshness_factor.py"
BACKTEST = ROOT / "scripts" / "backtest_r260_soft_freshness.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r260_soft_freshness_counts_prior_top30_and_caps_penalty() -> None:
    module = _load(BUILDER, "build_r260_soft_freshness_factor")
    days = [date(2024, 1, 2) + timedelta(days=index) for index in range(22)]
    symbols = ["A", *[f"S{index:02d}" for index in range(1, 31)]]
    rows = []
    for day in days:
        for rank, symbol in enumerate(symbols):
            rows.append(
                {
                    "date": day,
                    "symbol": symbol,
                    module.R257_FIELD: 1.0 - rank / 100.0,
                }
            )
    panel = pl.DataFrame(rows)
    members = pl.DataFrame(
        {
            "effective_date": [date(2020, 1, 1)] * len(symbols),
            "symbol": symbols,
        }
    )
    factor = module._recency_factor(panel, members).filter(pl.col("symbol") == "A").sort("date")
    values = factor[module.FIELD].to_list()
    assert math.isclose(float(values[0]), 1.0, abs_tol=1e-12)
    assert math.isclose(float(values[1]), 0.99, abs_tol=1e-12)
    assert math.isclose(float(values[20]), 0.8, abs_tol=1e-12)
    assert math.isclose(float(values[21]), 0.8, abs_tol=1e-12)
    valid = factor.filter(pl.col(module.FIELD).is_not_null())[module.FIELD]
    assert valid.min() >= 0.0
    assert valid.max() <= 1.0


def test_r260_prior_count_excludes_nonmembers() -> None:
    module = _load(BUILDER, "build_r260_soft_freshness_factor_nonmember")
    history = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)] * 3,
            "symbol": ["A", "B", "X", "A", "B", "X"],
            "_eligible": [True, True, False, True, True, False],
            module.R257_FIELD: [0.9, 0.5, 9.9, 0.8, 0.4, 9.9],
        }
    )
    counted = module._with_prior_top30_count(history).sort(["date", "symbol"])
    day2 = {
        row["symbol"]: row["_prior_top30_count"]
        for row in counted.filter(pl.col("date") == date(2024, 1, 3)).iter_rows(named=True)
    }
    assert day2 == {"A": 1, "B": 1, "X": 0}


def test_r260_soft_freshness_requires_r257_value() -> None:
    module = _load(BUILDER, "build_r260_soft_freshness_factor_missing")
    panel = pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)] * 3,
            "symbol": ["A", "B", "C", "A", "B", "C"],
            module.R257_FIELD: [0.9, 0.5, None, 0.8, 0.4, None],
        }
    )
    members = pl.DataFrame(
        {
            "effective_date": [date(2020, 1, 1)] * 3,
            "symbol": ["A", "B", "C"],
        }
    )
    factor = module._recency_factor(panel, members).sort(["date", "symbol"])
    missing = factor.filter(pl.col("symbol") == "C")[module.FIELD].to_list()
    assert missing == [None, None]
    valid = factor.filter(pl.col(module.FIELD).is_not_null())[module.FIELD]
    assert valid.min() >= 0.0
    assert valid.max() <= 1.0


def test_r260_candidate_and_control_strategies_load_and_match_universe() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_qrs_soft_fresh_h20_sl20")
    control = engine.get("ai_hs300_qrs_soft_fresh_control")
    new_factor_id = "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank"
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


def test_r260_backtest_contract_constants() -> None:
    module = _load(BACKTEST, "backtest_r260_soft_freshness")

    assert module.ROUND == "R260"
    assert module.FACTOR_ID == "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank"
    assert module.FACTOR_COLUMN == "quality_rs_soft_fresh_rank"
    assert module.R257_FACTOR_ID == "ext_alt_hs300_qrs_quality_relative_strength_min_rank"
    assert module.R257_COLUMN == "quality_relative_strength_min_rank"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_qrs_soft_fresh_h20_sl20",
        "control": "ai_hs300_qrs_soft_fresh_control",
    }
    assert module.OUT.name == "r260_soft_freshness.json"
    assert module.CACHE.name == "r260_cache"
    assert date(2023, 5, 4) == module.START
    assert [
        (date(2023, 5, 4), date(2024, 12, 31)),
        (date(2025, 1, 2), date(2026, 10, 16)),
    ] == module.SEGMENTS
