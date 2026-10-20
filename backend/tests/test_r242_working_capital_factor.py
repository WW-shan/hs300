from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r242_working_capital_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r242_working_capital_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r242_working_capital_formula_and_pit(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    periods = [
        date(2020, 3, 31), date(2020, 6, 30), date(2020, 9, 30), date(2020, 12, 31),
        date(2021, 3, 31), date(2021, 6, 30), date(2021, 9, 30), date(2021, 12, 31),
        date(2022, 3, 31), date(2022, 6, 30),
    ]
    ratios = [0.8, 0.3, 0.4, 0.5, 0.6, 0.7, 0.2, 0.1, 0.25, 0.15]
    assets = [100.0] * len(periods)
    receivable = [ratio * 100.0 for ratio in ratios]
    balance = pl.DataFrame({
        "symbol": ["A"] * len(periods),
        "period_end": periods,
        "accounts_receivable": receivable,
        "inventory": [0.0] * len(periods),
        "accounts_payable": [0.0] * len(periods),
        "total_assets": assets,
    })
    target = tmp_path / "financials"
    (target / "balance_sheet").mkdir(parents=True, exist_ok=True)
    balance.write_parquet(target / "balance_sheet" / "part.parquet")
    monkeypatch.setattr(base, "DATA", tmp_path)

    dates = [date(2022, 1, 1) + timedelta(days=i) for i in range(350)]
    frame = module._working_capital_change_yoy_low_daily(base, dates, ["A"]).sort("date")
    valid = frame.filter(pl.col(module.FIELD).is_not_null())
    assert valid.height > 0

    on_after = frame.filter(pl.col("date") == date(2022, 5, 3))[module.FIELD][0]
    assert math.isclose(float(on_after), 0.6 - 0.25, rel_tol=1e-12)

    cutoff = date(2022, 5, 31)
    prefix = module._working_capital_change_yoy_low_daily(
        base, [day for day in dates if day <= cutoff], ["A"]
    ).sort("date")
    full_prefix = frame.filter(pl.col("date") <= cutoff)
    assert prefix[module.FIELD].equals(full_prefix[module.FIELD])


def test_r242_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_working_capital_change_quality_h20_sl20")
    control = engine.get("ai_hs300_working_capital_change_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_wc_working_capital_change_yoy_low"] == 0.45
    assert "ext_alt_hs300_wc_working_capital_change_yoy_low" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_wc_working_capital_change_yoy_low": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]