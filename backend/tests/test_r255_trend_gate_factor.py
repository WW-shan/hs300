from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts" / "build_r255_trend_gate_factor.py"
BACKTEST = ROOT / "scripts" / "backtest_r255_trend_gate.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r255_gate_warmup_equal_close_and_above() -> None:
    module = _load(BUILDER, "build_r255_trend_gate_factor")
    dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(202)]
    closes = [10.0] * 200 + [10.0, 11.0]
    gate = module._build_gate(pl.DataFrame({"date": dates, "close": closes}))

    assert gate.columns == ["date", module.FIELD]
    assert gate[module.FIELD].null_count() == module.WINDOW - 1
    assert gate[module.FIELD][module.WINDOW - 1] == 0.0
    assert gate[module.FIELD][module.WINDOW] == 0.0
    assert gate[module.FIELD][module.WINDOW + 1] == 1.0
    assert gate[module.FIELD].drop_nulls().to_list() == [0.0, 0.0, 1.0]


def test_r255_gate_broadcast_is_date_constant_and_does_not_fill_missing_dates() -> None:
    module = _load(BUILDER, "build_r255_trend_gate_factor")
    first, second, missing = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
    grid = pl.DataFrame(
        {
            "symbol": ["A", "B", "A", "B", "A", "B"],
            "date": [first, first, second, second, missing, missing],
        }
    )
    gate = pl.DataFrame({"date": [first, second], module.FIELD: [0.0, 1.0]})
    result = module._broadcast_gate(grid, gate).sort(["date", "symbol"])

    for day, expected in ((first, 0.0), (second, 1.0)):
        values = result.filter(pl.col("date") == day)[module.FIELD].to_list()
        assert values == [expected, expected]
    assert result.filter(pl.col("date") == missing)[module.FIELD].null_count() == 2


def test_r255_candidate_and_control_strategies_load_and_gate_entries() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_minrank_ma200_gate_quality_h20_sl20")
    control = engine.get("ai_hs300_minrank_ma200_gate_quality_control")
    quality_factor = "ext_alt_hs300_minrank_management_ocf_gm_min_rank"
    gate_factor = "ext_alt_market_regime_hs300_above_ma200"

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"][quality_factor] == 0.45
    assert quality_factor not in control.meta["scoring"]
    assert candidate.meta["scoring"].get(gate_factor) is None
    assert control.meta["scoring"].get(gate_factor) is None
    assert candidate.required_features == control.required_features
    assert gate_factor in candidate.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)
    assert math.isclose(sum(control.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame(
        {
            "industry_l1": ["电子", "电子", "电子", "银行", "电子"],
            "ma60": [1.0, 1.0, 1.0, 1.0, 1.0],
            quality_factor: [0.5, None, 0.6, 0.7, 0.8],
            gate_factor: [1.0, 1.0, 0.0, 1.0, None],
        }
    )
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]


def test_r255_backtest_contract_constants() -> None:
    module = _load(BACKTEST, "backtest_r255_trend_gate")

    assert module.FACTOR_ID == "ext_alt_market_regime_hs300_above_ma200"
    assert module.FACTOR_COLUMN == "hs300_above_ma200"
    assert module.QUALITY_FACTOR_ID == "ext_alt_hs300_minrank_management_ocf_gm_min_rank"
    assert module.QUALITY_COLUMN == "management_ocf_gm_min_rank"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_minrank_ma200_gate_quality_h20_sl20",
        "control": "ai_hs300_minrank_ma200_gate_quality_control",
    }
    assert module.ROUND == "R255"
    assert module.OUT.name == "r255_trend_gate.json"
    assert module.CACHE.name == "r255_cache"
    assert module.GATE_WINDOW == 200
    assert module.MATCHING == "open_t+1"
    assert date(2023, 5, 4) == module.START
    assert [
        (date(2023, 5, 4), date(2024, 12, 31)),
        (date(2025, 1, 2), date(2026, 10, 16)),
    ] == module.SEGMENTS
