from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r215", SCRIPT)
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


def test_r215_market_skew_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    returns = [0.01 if i % 5 else -0.03 for i in range(99)]
    market = _price_panel(returns, "000300.SH", start)

    frame = builder._market_skew_frame(market, window_size=60)
    valid = frame.filter(pl.col("market_skew_60d_low").is_not_null())
    assert valid["date"][0] == frame["date"][60]
    assert valid.height == 40

    window = returns[-60:]
    mean = sum(window) / 60.0
    m2 = sum(value * value for value in window) / 60.0 - mean * mean
    m3 = (
        sum(value ** 3 for value in window) / 60.0
        - 3.0 * mean * (sum(value * value for value in window) / 60.0)
        + 2.0 * mean ** 3
    )
    expected = -m3 / m2 ** 1.5
    assert math.isclose(float(frame["market_skew_60d_low"][-1]), expected, rel_tol=1e-12)

    cutoff = frame["date"][70]
    prefix = builder._market_skew_frame(market.filter(pl.col("date") <= cutoff), window_size=60)
    assert prefix["market_skew_60d_low"].equals(frame.filter(pl.col("date") <= cutoff)["market_skew_60d_low"])

    flat = _price_panel([0.0] * 99, "000300.SH", start)
    flat_frame = builder._market_skew_frame(flat, window_size=60)
    assert flat_frame["market_skew_60d_low"].is_null().all()


def test_r215_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_market_skew_quality_gate_h20_sl20")
    control = engine.get("ai_hs300_market_skew_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"] == control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20