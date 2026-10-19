from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r209", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _price_panel(returns: list[float], symbol: str, start: date) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return pl.DataFrame({"symbol": symbol, "date": dates, "close": closes})


def _market_panel(returns: list[float], start: date) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [1000.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return pl.DataFrame({"date": dates, "close": closes})


def test_r209_price_delay_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    market_returns = [0.0005 + 0.008 * math.sin(i * 0.43) for i in range(99)]
    noise = [0.001 * math.sin(i * 1.17) for i in range(99)]
    high_lag = [
        1.1 * market_returns[i] + (0.7 * market_returns[i - 1] if i else 0.0) + noise[i]
        for i in range(99)
    ]
    low_lag = [
        1.1 * market_returns[i] + (0.1 * market_returns[i - 1] if i else 0.0) + noise[i]
        for i in range(99)
    ]

    market = _market_panel(market_returns, start)
    stocks = pl.concat([
        _price_panel(high_lag, "HIGH", start),
        _price_panel(low_lag, "LOW", start),
    ])
    frame = builder._price_delay_frame(stocks, market, window_size=60)
    high = frame.filter(pl.col("symbol") == "HIGH").sort("date")
    low = frame.filter(pl.col("symbol") == "LOW").sort("date")

    assert high.filter(pl.col("price_delay_60d").is_not_null())["date"][0] == high["date"][61]
    assert high["price_delay_60d"].is_not_null().sum() == 39
    assert low["price_delay_60d"].is_not_null().sum() == 39
    assert float(high["price_delay_60d"][-1]) > float(low["price_delay_60d"][-1])

    x = np.asarray(market_returns[-60:])
    lag = np.asarray(market_returns[-61:-1])
    y = np.asarray(high_lag[-60:])
    beta0 = np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1)
    beta1 = np.cov(y, lag, ddof=1)[0, 1] / np.var(lag, ddof=1)
    expected = beta1 / (abs(beta0) + abs(beta1) + 1e-6)
    assert math.isclose(float(high["price_delay_60d"][-1]), expected, rel_tol=1e-8, abs_tol=1e-10)

    cutoff = high["date"][70]
    prefix = builder._price_delay_frame(
        stocks.filter(pl.col("date") <= cutoff),
        market.filter(pl.col("date") <= cutoff),
        window_size=60,
    ).filter(pl.col("symbol") == "HIGH").sort("date")
    np.testing.assert_allclose(
        prefix["price_delay_60d"].to_numpy(),
        high.filter(pl.col("date") <= cutoff)["price_delay_60d"].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
        equal_nan=True,
    )


def test_r209_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_price_delay_quality_h20_sl20")
    control = engine.get("ai_hs300_price_delay_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_price_delay_60d"] == 0.45
    assert "ext_alt_hs300_price_delay_60d" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20
