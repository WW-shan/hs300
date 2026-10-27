from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r248_revenue_growth_accel_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r248_revenue_growth_accel_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_financial_fixture(tmp_path: Path) -> None:
    path = tmp_path / "financials" / "income"
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A"] * 8,
        "period_end": [
            date(2020, 3, 31),
            date(2020, 6, 30),
            date(2020, 9, 30),
            date(2020, 12, 31),
            date(2021, 3, 31),
            date(2021, 6, 30),
            date(2021, 9, 30),
            date(2021, 12, 31),
        ],
        "revenue": [100.0, 220.0, 360.0, 520.0, 130.0, 300.0, 510.0, 760.0],
    }).write_parquet(path / "part.parquet")


def test_r248_revenue_acceleration_formula_pit_and_staleness(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    _write_financial_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    quarterly = module._revenue_growth_accel_quarterly(base).sort("period_end")
    assert quarterly.height == 3
    values = {row["period_end"]: row[module.FIELD] for row in quarterly.iter_rows(named=True)}
    assert math.isclose(values[date(2021, 6, 30)], 7.0 / 60.0)
    assert math.isclose(values[date(2021, 9, 30)], 1.0 / 12.0)
    assert math.isclose(values[date(2021, 12, 31)], 0.0625)

    dates = [
        date(2021, 9, 2),
        date(2021, 9, 3),
        date(2021, 11, 2),
        date(2021, 11, 3),
        date(2022, 5, 2),
        date(2022, 5, 3),
        date(2023, 5, 15),
        date(2023, 5, 16),
    ]
    daily = module._revenue_growth_accel_daily(base, dates, ["A"]).sort("date")
    output = {row["date"]: row[module.FIELD] for row in daily.iter_rows(named=True)}
    assert output[date(2021, 9, 2)] is None
    assert math.isclose(float(output[date(2021, 9, 3)]), 7.0 / 60.0)
    assert math.isclose(float(output[date(2021, 11, 2)]), 7.0 / 60.0)
    assert math.isclose(float(output[date(2021, 11, 3)]), 1.0 / 12.0)
    assert math.isclose(float(output[date(2022, 5, 2)]), 1.0 / 12.0)
    assert math.isclose(float(output[date(2022, 5, 3)]), 0.0625)
    assert math.isclose(float(output[date(2023, 5, 15)]), 0.0625)
    assert output[date(2023, 5, 16)] is None


def test_r248_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_revenue_accel_quality_h20_sl20")
    control = engine.get("ai_hs300_revenue_accel_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_rga_revenue_growth_accel_q"] == 0.45
    assert "ext_alt_hs300_rga_revenue_growth_accel_q" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_rga_revenue_growth_accel_q": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]