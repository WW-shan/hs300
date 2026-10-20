from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r249_low_stock_skew_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r249_low_stock_skew_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_price_fixture(tmp_path: Path) -> tuple[list[date], list[float]]:
    dates = [date(2020, 1, 1) + timedelta(days=index) for index in range(62)]
    returns = [0.001] * 59 + [0.20, -0.50]
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    for index, day in enumerate(dates):
        target = tmp_path / "kline_daily_enriched" / f"date={day.isoformat()}"
        target.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({
            "symbol": ["A", "B"],
            "date": [day, day],
            "close": [prices[index], 100.0],
        }).write_parquet(target / "part.parquet")
    return dates, returns


def test_r249_rolling_skew_formula_warmup_and_degeneracy(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    dates, returns = _write_price_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    factor = module._stock_skew_daily(base, dates, ["A", "B"]).sort(["symbol", "date"])
    a = factor.filter(pl.col("symbol") == "A")
    b = factor.filter(pl.col("symbol") == "B")
    assert a.filter(pl.col(module.FIELD).is_not_null()).height == 2
    assert b.filter(pl.col(module.FIELD).is_not_null()).height == 0
    assert a[module.FIELD][-3] is None
    expected = -float(pl.Series(returns[:60]).skew())
    assert math.isclose(float(a[module.FIELD][-2]), expected, rel_tol=1e-12, abs_tol=1e-12)
    assert a[module.FIELD][-1] is not None
    assert not math.isclose(float(a[module.FIELD][-1]), expected, rel_tol=1e-12, abs_tol=1e-12)


def test_r249_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_low_stock_skew_quality_h20_sl20")
    control = engine.get("ai_hs300_low_stock_skew_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_skew_stock_skew_60d_low"] == 0.45
    assert "ext_alt_hs300_skew_stock_skew_60d_low" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_skew_stock_skew_60d_low": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]