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
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r210", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _hl_panel(half_ranges: list[float], symbol: str, start: date) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(half_ranges))]
    return pl.DataFrame({
        "symbol": symbol,
        "date": dates,
        "high": [100.0 * (1.0 + value) for value in half_ranges],
        "low": [100.0 * (1.0 - value) for value in half_ranges],
    })


def _spread(high: float, low: float, next_high: float, next_low: float) -> float:
    denominator = 3.0 - 2.0 * math.sqrt(2.0)
    beta = math.log(high / low) ** 2 + math.log(next_high / next_low) ** 2
    gamma = math.log(max(high, next_high) / min(low, next_low)) ** 2
    alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / denominator - math.sqrt(gamma / denominator)
    alpha = min(10.0, max(-10.0, alpha))
    value = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
    return max(0.0, value)


def test_r210_cs_spread_formula_warmup_direction_and_pit() -> None:
    builder = _load_builder()
    start = date(2025, 1, 1)
    wide = [0.025 + 0.004 * math.sin(i * 0.31) for i in range(100)]
    narrow = [0.006 + 0.001 * math.sin(i * 0.31) for i in range(100)]
    stocks = pl.concat([
        _hl_panel(wide, "WIDE", start),
        _hl_panel(narrow, "NARROW", start),
    ])

    frame = builder._corwin_schultz_spread_frame(stocks, window_size=60)
    wide_frame = frame.filter(pl.col("symbol") == "WIDE").sort("date")
    narrow_frame = frame.filter(pl.col("symbol") == "NARROW").sort("date")

    assert wide_frame.filter(pl.col("cs_spread_60d").is_not_null())["date"][0] == wide_frame["date"][60]
    assert wide_frame["cs_spread_60d"].is_not_null().sum() == 40
    assert narrow_frame["cs_spread_60d"].is_not_null().sum() == 40
    assert float(wide_frame["cs_spread_60d"][-1]) > float(narrow_frame["cs_spread_60d"][-1])

    wide_prices = stocks.filter(pl.col("symbol") == "WIDE").sort("date")
    expected_values = []
    for index in range(39, 99):
        expected_values.append(_spread(
            wide_prices["high"][index], wide_prices["low"][index],
            wide_prices["high"][index + 1], wide_prices["low"][index + 1],
        ))
    expected = float(np.mean(expected_values))
    actual = float(wide_frame["cs_spread_60d"][-1])
    assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)

    cutoff = wide_frame["date"][70]
    prefix = builder._corwin_schultz_spread_frame(
        stocks.filter(pl.col("date") <= cutoff), window_size=60
    ).filter(pl.col("symbol") == "WIDE").sort("date")
    np.testing.assert_allclose(
        prefix["cs_spread_60d"].to_numpy(),
        wide_frame.filter(pl.col("date") <= cutoff)["cs_spread_60d"].to_numpy(),
        rtol=1e-12,
        atol=1e-12,
        equal_nan=True,
    )


def test_r210_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_cs_spread_quality_h20_sl20")
    control = engine.get("ai_hs300_cs_spread_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_cs_spread_60d"] == 0.45
    assert "ext_alt_hs300_cs_spread_60d" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20
