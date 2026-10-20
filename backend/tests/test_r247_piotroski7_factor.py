from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r247_piotroski7_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r247_piotroski7_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_financial_fixture(tmp_path: Path) -> None:
    for name in ("income", "balance_sheet", "cash_flow"):
        (tmp_path / "financials" / name).mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A", "A", "A"],
        "period_end": [date(2020, 12, 31), date(2021, 12, 31), date(2022, 12, 31)],
        "revenue": [100.0, 120.0, 90.0],
        "operating_costs": [60.0, 60.0, 70.0],
        "net_income": [10.0, 12.0, 5.0],
    }).write_parquet(tmp_path / "financials" / "income" / "part.parquet")
    pl.DataFrame({
        "symbol": ["A", "A", "A"],
        "period_end": [date(2020, 12, 31), date(2021, 12, 31), date(2022, 12, 31)],
        "total_assets": [1000.0, 1050.0, 1100.0],
        "total_liabilities": [400.0, 390.0, 450.0],
    }).write_parquet(tmp_path / "financials" / "balance_sheet" / "part.parquet")
    pl.DataFrame({
        "symbol": ["A", "A", "A"],
        "period_end": [date(2020, 12, 31), date(2021, 12, 31), date(2022, 12, 31)],
        "net_operating_cash_flow": [12.0, 15.0, 3.0],
    }).write_parquet(tmp_path / "financials" / "cash_flow" / "part.parquet")


def test_r247_score_pit_components_and_staleness(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    _write_financial_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    annual = module._piotroski7_annual(base).sort("period_end")
    assert annual.height == 2
    assert annual.filter(pl.col("period_end") == date(2021, 12, 31))["effective_date"][0] == date(2022, 5, 3)
    assert math.isclose(float(annual.filter(pl.col("period_end") == date(2021, 12, 31))[module.FIELD][0]), 1.0)
    assert math.isclose(float(annual.filter(pl.col("period_end") == date(2022, 12, 31))[module.FIELD][0]), 2.0 / 7.0)

    dates = [
        date(2022, 5, 2),
        date(2022, 5, 3),
        date(2023, 5, 2),
        date(2023, 5, 3),
        date(2024, 8, 1),
    ]
    daily = module._piotroski7_daily(base, dates, ["A"]).sort("date")
    values = {row["date"]: row[module.FIELD] for row in daily.iter_rows(named=True)}
    assert values[date(2022, 5, 2)] is None
    assert math.isclose(float(values[date(2022, 5, 3)]), 1.0)
    assert math.isclose(float(values[date(2023, 5, 2)]), 1.0)
    assert math.isclose(float(values[date(2023, 5, 3)]), 2.0 / 7.0)
    assert values[date(2024, 8, 1)] is None

    monkeypatch.setattr(module, "DATA", tmp_path)
    module_daily = module._piotroski7_daily(module, dates, ["A"]).sort("date")
    assert math.isclose(float(module_daily[module.FIELD][1]), 1.0)


def test_r247_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_piotroski7_quality_h20_sl20")
    control = engine.get("ai_hs300_piotroski7_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_pf7_piotroski7_score"] == 0.45
    assert "ext_alt_hs300_pf7_piotroski7_score" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_pf7_piotroski7_score": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]