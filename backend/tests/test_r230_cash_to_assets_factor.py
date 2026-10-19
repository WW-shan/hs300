from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r230", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r230_cash_to_assets_formula_and_pit(tmp_path: Path, monkeypatch) -> None:
    builder = _load_builder()
    periods = [
        date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31),
        date(2022, 3, 31), date(2022, 6, 30),
    ]
    # distinct ratio per quarter: 0.1, 0.2, 0.3, 0.4, 0.5, 0.6
    cash = [10.0, 40.0, 90.0, 160.0, 250.0, 360.0]
    assets = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0]
    announce = [None, None, None, None, date(2022, 4, 25), None]
    balance = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "announce_date": announce,
        "cash_and_equivalents": cash,
        "total_assets": assets,
    })
    target = tmp_path / "financials"
    (target / "balance_sheet").mkdir(parents=True, exist_ok=True)
    balance.write_parquet(target / "balance_sheet" / "part.parquet")
    monkeypatch.setattr(builder, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(400)]
    frame = builder._cash_to_assets_daily(dates, ["A"]).sort("date")
    valid = frame.filter(pl.col("cash_to_assets").is_not_null())
    assert valid.height > 0
    assert valid["date"].min() <= date(2022, 6, 30)

    # Statutory deadline + 3 days: FY2021 and 2022Q1 both become effective 2022-05-03.
    # Before that date the latest available report is 2021Q3 (0.3).
    before = float(frame.filter(pl.col("date") == date(2022, 5, 2))["cash_to_assets"][0])
    assert math.isclose(before, 0.3, rel_tol=1e-12)
    # On the shared effective date the later period end (2022Q1, 0.5) wins.
    on_after = float(frame.filter(pl.col("date") == date(2022, 5, 3))["cash_to_assets"][0])
    assert math.isclose(on_after, 0.5, rel_tol=1e-12)
    # Provider announce dates are ignored by design, so 2022Q1 is not visible on 2022-04-26.
    q1 = float(frame.filter(pl.col("date") == date(2022, 4, 26))["cash_to_assets"][0])
    assert math.isclose(q1, 0.3, rel_tol=1e-12)

    cutoff = date(2022, 6, 30)
    prefix = builder._cash_to_assets_daily([day for day in dates if day <= cutoff], ["A"]).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix["cash_to_assets"].equals(full_prefix["cash_to_assets"])


def test_r230_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_cash_to_assets_quality_h20_sl20")
    control = engine.get("ai_hs300_cash_to_assets_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_cash_to_assets"] == 0.45
    assert "ext_alt_hs300_cash_to_assets" not in control.meta["scoring"]
    assert "industry_l1" in candidate.required_features
    assert "industry_l1" in control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_cash_to_assets": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
