from __future__ import annotations

import importlib.util
import math
import statistics as st
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r214", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _price_panel(returns: list[float], symbol: str, start: date) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return pl.DataFrame({"symbol": [symbol] * len(dates), "date": dates, "close": closes})


def test_r214_risk_adjusted_momentum_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    smooth = [0.01 + 0.0001 * math.sin(i * 0.7) for i in range(99)]
    choppy = [0.02 if i % 2 == 0 else -0.005 for i in range(99)]
    smooth_prices = _price_panel(smooth, "SMOOTH", start)
    choppy_prices = _price_panel(choppy, "CHOPPY", start)
    stocks = pl.concat([smooth_prices, choppy_prices])

    frame = builder._risk_adjusted_momentum_frame(stocks, window_size=60)
    smooth_frame = frame.filter(pl.col("symbol") == "SMOOTH").sort("date")
    choppy_frame = frame.filter(pl.col("symbol") == "CHOPPY").sort("date")
    assert smooth_frame.filter(pl.col("risk_adjusted_momentum_60d").is_not_null())["date"][0] == smooth_frame["date"][60]
    assert smooth_frame["risk_adjusted_momentum_60d"].is_not_null().sum() == 40
    assert choppy_frame["risk_adjusted_momentum_60d"].is_not_null().sum() == 40
    assert float(smooth_frame["risk_adjusted_momentum_60d"][-1]) > float(choppy_frame["risk_adjusted_momentum_60d"][-1])

    closes = smooth_prices["close"].to_list()
    expected_net = closes[-1] / closes[-61] - 1.0
    expected_vol = st.stdev(smooth[-60:]) * math.sqrt(252.0)
    smooth_last = float(smooth_frame["risk_adjusted_momentum_60d"][-1])
    assert math.isclose(smooth_last, expected_net / expected_vol, rel_tol=1e-12)

    cutoff = smooth_frame["date"][70]
    prefix = builder._risk_adjusted_momentum_frame(
        stocks.filter(pl.col("date") <= cutoff),
        window_size=60,
    ).filter(pl.col("symbol") == "SMOOTH").sort("date")
    assert prefix["risk_adjusted_momentum_60d"].equals(
        smooth_frame.filter(pl.col("date") <= cutoff)["risk_adjusted_momentum_60d"]
    )

    flat = _price_panel([0.0] * 99, "FLAT", start)
    flat_frame = builder._risk_adjusted_momentum_frame(flat, window_size=60)
    assert flat_frame["risk_adjusted_momentum_60d"].is_null().all()


def test_r214_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_risk_adjusted_momentum_quality_h20_sl20")
    control = engine.get("ai_hs300_risk_adjusted_momentum_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_risk_adjusted_momentum_60d"] == 0.45
    assert "ext_alt_hs300_risk_adjusted_momentum_60d" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20