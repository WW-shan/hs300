from __future__ import annotations

import importlib.util
import math
import statistics as st
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r216", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r216_cash_flow_stability_formula_pit_and_warmup(tmp_path: Path, monkeypatch) -> None:
    builder = _load_builder()
    periods = [date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30)]
    cumulative_ocf = [10.0, 30.0, 60.0, 100.0, 5.0, 25.0]
    balance = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "total_assets": [100.0] * len(periods),
    })
    cash = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "net_operating_cash_flow": cumulative_ocf,
    })
    for name, frame in (("balance_sheet", balance), ("cash_flow", cash)):
        target = tmp_path / "financials" / name
        target.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(target / "part.parquet")
    monkeypatch.setattr(builder, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(365)]
    frame = builder._cash_flow_stability_daily(dates, ["A"]).sort("date")
    valid = frame.filter(pl.col("cash_flow_stability_4q_low").is_not_null())
    assert valid.height > 0
    assert valid["date"].min() <= date(2022, 6, 30)
    assert (valid["cash_flow_stability_4q_low"] <= 0.0).all()

    quarterly = [0.10, 0.20, 0.30, 0.40, 0.05, 0.20]
    expected = -st.pstdev(quarterly[-4:])
    actual = float(frame.filter(pl.col("date") == date(2022, 12, 31))["cash_flow_stability_4q_low"][0])
    assert math.isclose(actual, expected, rel_tol=1e-12)

    cutoff = date(2022, 6, 30)
    prefix = builder._cash_flow_stability_daily([day for day in dates if day <= cutoff], ["A"]).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix["cash_flow_stability_4q_low"].equals(full_prefix["cash_flow_stability_4q_low"])


def test_r216_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_cash_flow_stability_quality_h20_sl20")
    control = engine.get("ai_hs300_cash_flow_stability_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_cash_flow_stability_4q_low"] == 0.45
    assert "ext_alt_hs300_cash_flow_stability_4q_low" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20