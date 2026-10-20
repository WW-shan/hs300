from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r244_gross_profitability_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r244_gross_profitability_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r244_gross_profitability_formula_and_pit(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    periods = [
        date(2020, 3, 31), date(2020, 6, 30), date(2020, 9, 30), date(2020, 12, 31),
        date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31),
        date(2022, 3, 31),
    ]
    revenue = [100.0, 200.0, 300.0, 400.0, 100.0, 200.0, 300.0, 400.0, 100.0]
    costs = [60.0, 120.0, 180.0, 240.0, 60.0, 120.0, 180.0, 240.0, 60.0]
    income = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "revenue": revenue,
        "operating_costs": costs,
    })
    balance = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "total_assets": [800.0] * len(periods),
    })
    target = tmp_path / "financials"
    (target / "income").mkdir(parents=True, exist_ok=True)
    (target / "balance_sheet").mkdir(parents=True, exist_ok=True)
    income.write_parquet(target / "income" / "part.parquet")
    balance.write_parquet(target / "balance_sheet" / "part.parquet")
    monkeypatch.setattr(base, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(350)]
    frame = module._gross_profitability_daily(base, dates, ["A"]).sort("date")
    valid = frame.filter(pl.col(module.FIELD).is_not_null())
    assert valid.height > 0

    on_after = frame.filter(pl.col("date") == date(2022, 5, 3))[module.FIELD][0]
    assert math.isclose(float(on_after), (400.0 - 240.0) / 800.0, rel_tol=1e-12)

    cutoff = date(2022, 5, 31)
    prefix = module._gross_profitability_daily(
        base, [day for day in dates if day <= cutoff], ["A"]
    ).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix[module.FIELD].equals(full_prefix[module.FIELD])


def test_r244_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_gross_profitability_h20_sl20")
    control = engine.get("ai_hs300_gross_profitability_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_gp_gross_profitability"] == 0.45
    assert "ext_alt_hs300_gp_gross_profitability" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_gp_gross_profitability": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]