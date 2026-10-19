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
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r212", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _price_panel(returns: list[float], symbol: str | None, start: date) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    data = {"date": dates, "close": closes}
    if symbol is not None:
        data["symbol"] = [symbol] * len(dates)
    return pl.DataFrame(data)


def test_r212_market_down_beta_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    market_returns = [0.0003 + 0.012 * math.sin(i * 0.47) for i in range(99)]
    noise = [0.0015 * math.cos(i * 1.13) for i in range(99)]
    high_beta = [1.6 * value + noise[i] for i, value in enumerate(market_returns)]
    low_beta = [0.35 * value + noise[i] for i, value in enumerate(market_returns)]
    stocks = pl.concat([
        _price_panel(high_beta, "HIGH", start),
        _price_panel(low_beta, "LOW", start),
    ])
    market = _price_panel(market_returns, None, start)

    frame = builder._market_down_beta_frame(stocks, market, window_size=60, min_down_days=12)
    high = frame.filter(pl.col("symbol") == "HIGH").sort("date")
    low = frame.filter(pl.col("symbol") == "LOW").sort("date")
    assert high.filter(pl.col("market_down_beta_60d_low").is_not_null())["date"][0] == high["date"][60]
    assert high["market_down_beta_60d_low"].is_not_null().sum() == 40
    assert low["market_down_beta_60d_low"].is_not_null().sum() == 40
    assert float(high["market_down_beta_60d_low"][-1]) < float(low["market_down_beta_60d_low"][-1])

    x = np.asarray(market_returns[-60:])
    y = np.asarray(high_beta[-60:])
    mask = x < 0.0
    xd = x[mask]
    yd = y[mask]
    expected_beta = float(np.cov(yd, xd, ddof=1)[0, 1] / np.var(xd, ddof=1))
    assert math.isclose(float(high["market_down_beta_60d_low"][-1]), -expected_beta, rel_tol=1e-9, abs_tol=1e-10)

    cutoff = high["date"][70]
    prefix = builder._market_down_beta_frame(
        stocks.filter(pl.col("date") <= cutoff),
        market.filter(pl.col("date") <= cutoff),
        window_size=60,
        min_down_days=12,
    ).filter(pl.col("symbol") == "HIGH").sort("date")
    np.testing.assert_allclose(
        prefix["market_down_beta_60d_low"].to_numpy(),
        high.filter(pl.col("date") <= cutoff)["market_down_beta_60d_low"].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
        equal_nan=True,
    )


def test_r212_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_market_down_beta_quality_h20_sl20")
    control = engine.get("ai_hs300_market_down_beta_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_market_down_beta_60d_low"] == 0.45
    assert "ext_alt_hs300_market_down_beta_60d_low" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20
