from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r250_revenue_stability_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r250_revenue_stability_factor", SCRIPT)
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
            date(2019, 3, 31), date(2019, 6, 30), date(2019, 9, 30), date(2019, 12, 31),
            date(2020, 3, 31), date(2020, 6, 30), date(2020, 9, 30), date(2020, 12, 31),
        ],
        "revenue": [100.0, 220.0, 360.0, 520.0, 130.0, 300.0, 510.0, 760.0],
    }).write_parquet(path / "part.parquet")


def test_r250_revenue_stability_formula_pit_and_staleness(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    _write_financial_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    quarterly = module._revenue_stability_quarterly(base)
    assert quarterly.height == 1
    growths = [0.30, 25.0 / 60.0, 0.50, 0.5625]
    expected = -float(pl.Series(growths).std())
    assert quarterly["effective_date"][0] == date(2021, 5, 3)
    assert math.isclose(float(quarterly[module.FIELD][0]), expected, rel_tol=1e-12, abs_tol=1e-12)

    dates = [date(2021, 5, 2), date(2021, 5, 3), date(2022, 5, 15), date(2022, 5, 16)]
    daily = module._revenue_stability_daily(base, dates, ["A"]).sort("date")
    values = {row["date"]: row[module.FIELD] for row in daily.iter_rows(named=True)}
    assert values[date(2021, 5, 2)] is None
    assert math.isclose(float(values[date(2021, 5, 3)]), expected, rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(float(values[date(2022, 5, 15)]), expected, rel_tol=1e-12, abs_tol=1e-12)
    assert values[date(2022, 5, 16)] is None


def test_r250_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_revenue_stability_quality_h20_sl20")
    control = engine.get("ai_hs300_revenue_stability_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_revstab_revenue_growth_stability_4q_low"] == 0.45
    assert "ext_alt_hs300_revstab_revenue_growth_stability_4q_low" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_revstab_revenue_growth_stability_4q_low": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]