from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r229", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r229_ocf_margin_formula_and_pit(tmp_path: Path, monkeypatch) -> None:
    builder = _load_builder()
    periods = [
        date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31),
        date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30), date(2022, 12, 31),
    ]
    cumulative_revenue = [100.0, 220.0, 360.0, 500.0, 120.0, 260.0, 420.0, 600.0]
    cumulative_ocf = [10.0, 30.0, 60.0, 90.0, 15.0, 40.0, 80.0, 130.0]
    income = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "revenue": cumulative_revenue,
    })
    cash = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "net_operating_cash_flow": cumulative_ocf,
    })
    target = tmp_path / "financials"
    (target / "income").mkdir(parents=True, exist_ok=True)
    (target / "cash_flow").mkdir(parents=True, exist_ok=True)
    income.write_parquet(target / "income" / "part.parquet")
    cash.write_parquet(target / "cash_flow" / "part.parquet")
    monkeypatch.setattr(builder, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(500)]
    frame = builder._ocf_margin_4q_daily(dates, ["A"]).sort("date")
    valid = frame.filter(pl.col("ocf_margin_4q").is_not_null())
    assert valid.height > 0
    assert valid["date"].min() <= date(2022, 6, 30)

    ttm_ocf = 15.0 + 25.0 + 40.0 + 50.0
    ttm_revenue = 120.0 + 140.0 + 160.0 + 180.0
    expected = ttm_ocf / ttm_revenue
    actual = float(
        frame.filter(pl.col("date") == date(2023, 5, 4))["ocf_margin_4q"][0]
    )
    assert math.isclose(actual, expected, rel_tol=1e-12)

    cutoff = date(2022, 6, 30)
    prefix = builder._ocf_margin_4q_daily(
        [day for day in dates if day <= cutoff], ["A"]
    ).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix["ocf_margin_4q"].equals(full_prefix["ocf_margin_4q"])


def test_r229_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_ocf_margin_quality_h20_sl20")
    control = engine.get("ai_hs300_ocf_margin_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_ocf_margin_4q"] == 0.45
    assert "ext_alt_hs300_ocf_margin_4q" not in control.meta["scoring"]
    assert "industry_l1" in candidate.required_features
    assert "industry_l1" in control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_ocf_margin_4q": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
