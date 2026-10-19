from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r220", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r220_management_expense_formula_pit_and_warmup(tmp_path: Path, monkeypatch) -> None:
    builder = _load_builder()
    periods = [date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31), date(2022, 3, 31), date(2022, 6, 30)]
    cumulative_revenue = [100.0, 220.0, 360.0, 500.0, 120.0, 260.0]
    cumulative_management = [5.0, 11.0, 18.0, 25.0, 6.0, 13.0]
    income = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "revenue": cumulative_revenue,
        "management_expenses": cumulative_management,
    })
    target = tmp_path / "financials" / "income"
    target.mkdir(parents=True, exist_ok=True)
    income.write_parquet(target / "part.parquet")
    monkeypatch.setattr(builder, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(365)]
    frame = builder._management_expense_ratio_4q_daily(dates, ["A"]).sort("date")
    valid = frame.filter(pl.col("management_expense_ratio_4q_low").is_not_null())
    assert valid.height > 0
    assert valid["date"].min() <= date(2022, 6, 30)

    actual = float(frame.filter(pl.col("date") == date(2022, 12, 31))["management_expense_ratio_4q_low"][0])
    assert math.isclose(actual, -0.05, rel_tol=1e-12)

    cutoff = date(2022, 6, 30)
    prefix = builder._management_expense_ratio_4q_daily([day for day in dates if day <= cutoff], ["A"]).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix["management_expense_ratio_4q_low"].equals(full_prefix["management_expense_ratio_4q_low"])


def test_r220_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_management_efficiency_quality_h20_sl20")
    control = engine.get("ai_hs300_management_efficiency_quality_control")
    assert candidate is not None
    assert control is not None
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_management_expense_ratio_4q_low"] == 0.45
    assert "ext_alt_hs300_management_expense_ratio_4q_low" not in control.meta["scoring"]
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20