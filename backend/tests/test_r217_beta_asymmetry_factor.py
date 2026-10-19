from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r217", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _panels() -> tuple[pl.DataFrame, pl.DataFrame, list[date]]:
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(160)]
    pattern = [0.01, 0.02, -0.01, -0.02]
    market_returns = [pattern[i % len(pattern)] for i in range(len(dates))]
    stock_returns = [2.0 * value if value > 0 else 0.5 * value for value in market_returns]

    market_values = [100.0]
    stock_values = [100.0]
    for market_ret, stock_ret in zip(market_returns[1:], stock_returns[1:], strict=True):
        market_values.append(market_values[-1] * (1.0 + market_ret))
        stock_values.append(stock_values[-1] * (1.0 + stock_ret))

    prices = pl.DataFrame({
        "symbol": ["A"] * len(dates),
        "date": dates,
        "close": stock_values,
    })
    market = pl.DataFrame({"date": dates, "close": market_values})
    return prices, market, dates


def test_r217_beta_asymmetry_formula_and_pit() -> None:
    builder = _load_builder()
    prices, market, dates = _panels()
    frame = builder._beta_asymmetry_frame(prices, market).sort("date")
    valid = frame.filter(pl.col("beta_asymmetry_120d").is_not_null())

    assert valid.height > 0
    assert valid["date"].min() == dates[120]
    assert all(math.isclose(float(value), 1.5, rel_tol=1e-9, abs_tol=1e-9) for value in valid["beta_asymmetry_120d"])

    cutoff = dates[130]
    prefix = builder._beta_asymmetry_frame(
        prices.filter(pl.col("date") <= cutoff),
        market.filter(pl.col("date") <= cutoff),
    )
    prefix_value = prefix.filter(pl.col("date") == cutoff)["beta_asymmetry_120d"][0]
    full_value = frame.filter(pl.col("date") == cutoff)["beta_asymmetry_120d"][0]
    assert math.isclose(float(prefix_value), float(full_value), rel_tol=1e-12, abs_tol=1e-12)


def test_r217_requires_both_market_states() -> None:
    builder = _load_builder()
    prices, market, dates = _panels()
    market_up = market.with_columns(
        pl.Series("close", [100.0 * (1.001 ** i) for i in range(len(dates))])
    )
    frame = builder._beta_asymmetry_frame(prices, market_up)
    assert frame["beta_asymmetry_120d"].is_null().all()


def test_r217_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_beta_asymmetry_quality_h20_sl20")
    control = engine.get("ai_hs300_beta_asymmetry_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_beta_asymmetry_120d"] == 0.45
    assert "ext_alt_hs300_beta_asymmetry_120d" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20