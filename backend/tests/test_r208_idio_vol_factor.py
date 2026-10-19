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
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r208", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _prices(returns: list[float], start: date) -> tuple[list[date], list[float]]:
    dates = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return dates, closes


def test_r208_idio_vol_formula_warmup_and_direction() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    market_returns = [0.0004 + 0.009 * math.sin(i * 0.61) for i in range(99)]
    low_noise = [0.0015 * math.sin(i * 1.27) for i in range(99)]
    high_noise = [0.008 * math.cos(i * 0.93) for i in range(99)]
    low_returns = [1.2 * m + n for m, n in zip(market_returns, low_noise, strict=True)]
    high_returns = [0.9 * m + n for m, n in zip(market_returns, high_noise, strict=True)]

    dates, market_close = _prices(market_returns, start)
    _, low_close = _prices(low_returns, start)
    _, high_close = _prices(high_returns, start)
    market = pl.DataFrame({"date": dates, "close": market_close})
    stocks = pl.concat([
        pl.DataFrame({"symbol": "LOW", "date": dates, "close": low_close}),
        pl.DataFrame({"symbol": "HIGH", "date": dates, "close": high_close}),
    ])

    frame = builder._residual_volatility_frame(stocks, market, window_size=60)
    low = frame.filter(pl.col("symbol") == "LOW").sort("date")
    high = frame.filter(pl.col("symbol") == "HIGH").sort("date")

    assert low.filter(pl.col("idio_vol_60d_low").is_not_null())["date"][0] == dates[60]
    assert low["idio_vol_60d_low"].is_not_null().sum() == 40
    assert high["idio_vol_60d_low"].is_not_null().sum() == 40

    x = np.asarray(market_returns[-60:])
    y = np.asarray(low_returns[-60:])
    residual_var = np.var(y, ddof=1) - np.cov(x, y, ddof=1)[0, 1] ** 2 / np.var(x, ddof=1)
    expected = -math.sqrt(max(float(residual_var), 0.0)) * math.sqrt(252.0)
    actual = float(low["idio_vol_60d_low"][-1])
    assert math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-10)

    # 高噪声股票的残差波动更大，因此反向后的因子值应更低。
    assert float(low["idio_vol_60d_low"][-1]) > float(high["idio_vol_60d_low"][-1])


def test_r208_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_idio_vol_quality_h20_sl20")
    control = engine.get("ai_hs300_quality_cash_h20_sl20_control")

    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_idio_vol_60d_low"] == 0.45
    assert "ext_alt_hs300_idio_vol_60d_low" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20
