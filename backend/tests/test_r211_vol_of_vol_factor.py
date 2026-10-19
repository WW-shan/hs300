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
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r211", SCRIPT)
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


def test_r211_vol_of_vol_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    stable = [0.0005 + 0.005 * math.sin(i * 0.31) for i in range(99)]
    volatile = [
        0.0005 + 0.018 * math.sin(i * 0.31) * (1.0 + 0.6 * math.cos(i * 0.73))
        for i in range(99)
    ]
    stocks = pl.concat([
        _price_panel(stable, "STABLE", start),
        _price_panel(volatile, "VOLATILE", start),
    ])
    frame = builder._vol_of_vol_frame(stocks, vol_window=20, stability_window=60)
    stable_frame = frame.filter(pl.col("symbol") == "STABLE").sort("date")
    volatile_frame = frame.filter(pl.col("symbol") == "VOLATILE").sort("date")

    assert stable_frame.filter(pl.col("vol_of_vol_60d_low").is_not_null())["date"][0] == stable_frame["date"][79]
    assert stable_frame["vol_of_vol_60d_low"].is_not_null().sum() == 21
    assert volatile_frame["vol_of_vol_60d_low"].is_not_null().sum() == 21
    assert float(stable_frame["vol_of_vol_60d_low"][-1]) > float(volatile_frame["vol_of_vol_60d_low"][-1])

    rv = []
    for index in range(20, len(stable)):
        window = np.asarray(stable[index - 19:index + 1])
        rv.append(float(np.std(window, ddof=1) * math.sqrt(252.0)))
    expected = -float(np.std(rv[-60:], ddof=1))
    actual = float(stable_frame["vol_of_vol_60d_low"][-1])
    assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)

    cutoff = stable_frame["date"][85]
    prefix = builder._vol_of_vol_frame(
        stocks.filter(pl.col("date") <= cutoff), vol_window=20, stability_window=60
    ).filter(pl.col("symbol") == "STABLE").sort("date")
    np.testing.assert_allclose(
        prefix["vol_of_vol_60d_low"].to_numpy(),
        stable_frame.filter(pl.col("date") <= cutoff)["vol_of_vol_60d_low"].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
        equal_nan=True,
    )


def test_r211_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_vol_of_vol_quality_h20_sl20")
    control = engine.get("ai_hs300_vol_of_vol_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_vol_of_vol_60d_low"] == 0.45
    assert "ext_alt_hs300_vol_of_vol_60d_low" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20
