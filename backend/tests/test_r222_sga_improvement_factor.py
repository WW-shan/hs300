from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_r222", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r222_sga_qoq_improvement_formula_and_pit(tmp_path: Path, monkeypatch) -> None:
    builder = _load_builder()
    periods = [
        date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30),
        date(2021, 12, 31), date(2022, 3, 31),
    ]
    cumulative_revenue = [100.0, 220.0, 360.0, 500.0, 160.0]
    cumulative_selling = [10.0, 22.0, 36.0, 50.0, 12.0]
    cumulative_management = [5.0, 11.0, 18.0, 25.0, 6.0]
    income = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "revenue": cumulative_revenue,
        "selling_expenses": cumulative_selling,
        "management_expenses": cumulative_management,
    })
    target = tmp_path / "financials" / "income"
    target.mkdir(parents=True, exist_ok=True)
    income.write_parquet(target / "part.parquet")
    monkeypatch.setattr(builder, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(365)]
    frame = builder._sga_ratio_4q_daily(dates, ["A"]).sort("date")
    valid = frame.filter(pl.col("sga_ratio_4q_qoq_improvement").is_not_null())
    assert valid.height > 0
    assert valid["date"].min() <= date(2022, 6, 30)

    actual = float(
        frame.filter(pl.col("date") == date(2022, 12, 31))[
            "sga_ratio_4q_qoq_improvement"
        ][0]
    )
    assert math.isclose(actual, 0.0107142857142857, rel_tol=1e-12)

    cutoff = date(2022, 6, 30)
    prefix = builder._sga_ratio_4q_daily(
        [day for day in dates if day <= cutoff], ["A"]
    ).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix["sga_ratio_4q_qoq_improvement"].equals(
        full_prefix["sga_ratio_4q_qoq_improvement"]
    )


def test_r222_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_sga_nonfin_qoq_improvement_h20_sl20")
    control = engine.get("ai_hs300_sga_nonfin_qoq_improvement_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_sga_ratio_4q_qoq_improvement"] == 0.45
    assert "ext_alt_hs300_sga_ratio_4q_qoq_improvement" not in control.meta["scoring"]
    assert "industry_l1" in candidate.required_features
    assert "industry_l1" in control.required_features
    assert candidate.max_hold_days == 20
    assert control.max_hold_days == 20
    assert candidate.stop_loss == -0.20
    assert control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_sga_ratio_4q_qoq_improvement": [0.01, 0.02, 0.03, 0.04],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]