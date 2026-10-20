from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r246_free_cash_flow_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r246_free_cash_flow_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_financial_fixture(tmp_path: Path) -> None:
    cash_dir = tmp_path / "financials" / "cash_flow"
    cash_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A", "A", "A", "A"],
        "period_end": [date(2020, 12, 31), date(2021, 12, 31), date(2022, 3, 31), date(2022, 12, 31)],
        "net_operating_cash_flow": [80.0, 120.0, 30.0, 150.0],
    }).write_parquet(cash_dir / "part.parquet")

    capex_dir = tmp_path / "research" / "r86_intangible"
    capex_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A", "A", "A", "A"],
        "period_end": [date(2020, 12, 31), date(2021, 12, 31), date(2022, 3, 31), date(2022, 12, 31)],
        "notice_date": [date(2021, 3, 1), date(2022, 6, 10), date(2022, 4, 20), date(2023, 3, 1)],
        "total_assets": [1000.0, 1000.0, 1100.0, 1000.0],
        "capex": [10.0, 20.0, 5.0, 30.0],
    }).write_parquet(capex_dir / "A.parquet")


def test_r246_free_cash_flow_pit_and_staleness(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    _write_financial_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    annual = module._free_cash_flow_annual(base).sort("period_end")
    assert annual.height == 3
    assert annual.filter(pl.col("period_end") == date(2021, 12, 31))["effective_date"][0] == date(2022, 6, 11)
    assert annual.filter(pl.col("period_end") == date(2022, 12, 31))["effective_date"][0] == date(2023, 5, 3)

    dates = [
        date(2022, 6, 10),
        date(2022, 6, 11),
        date(2023, 5, 2),
        date(2023, 5, 3),
        date(2024, 8, 1),
    ]
    daily = module._free_cash_flow_daily(base, dates, ["A"]).sort("date")
    values = {row["date"]: row[module.FIELD] for row in daily.iter_rows(named=True)}

    assert values[date(2022, 6, 10)] is None
    assert math.isclose(float(values[date(2022, 6, 11)]), 0.10, rel_tol=1e-12)
    assert math.isclose(float(values[date(2023, 5, 2)]), 0.10, rel_tol=1e-12)
    assert math.isclose(float(values[date(2023, 5, 3)]), 0.12, rel_tol=1e-12)
    assert values[date(2024, 8, 1)] is None

    monkeypatch.setattr(module, "DATA", tmp_path)
    module_daily = module._free_cash_flow_daily(module, dates, ["A"]).sort("date")
    assert module_daily[module.FIELD].to_list()[:2] == [None, 0.10]


def test_r246_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_fcf_assets_quality_h20_sl20")
    control = engine.get("ai_hs300_fcf_assets_quality_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_fcf_free_cash_flow_assets"] == 0.45
    assert "ext_alt_hs300_fcf_free_cash_flow_assets" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_fcf_free_cash_flow_assets": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
def test_r246_backtest_contract_pit_audit_and_ic_helpers() -> None:
    spec = importlib.util.spec_from_file_location(
        "backtest_r246_free_cash_flow",
        ROOT / "scripts" / "backtest_r246_free_cash_flow.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.FACTOR_ID == "ext_alt_hs300_fcf_free_cash_flow_assets"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_fcf_assets_quality_h20_sl20",
        "control": "ai_hs300_fcf_assets_quality_control",
    }
    assert "net_financing_4q_low" in module.BASE_EXT_FIELDS
    assert "gross_profitability" in module.CORRELATION_FIELDS
    assert module.OUT.name == "r246_free_cash_flow.json"
    assert module.CACHE.name == "r246_cache"

    expected = pl.DataFrame({
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "symbol": ["A", "A"],
        module.FACTOR_COLUMN: [0.10, 0.12],
    })
    audit = module._pit_audit(expected, object(), expected)
    assert audit["all_checks_pass"] is True
    assert audit["missing_written_rows"] == 0
    assert audit["unexpected_written_rows"] == 0
    corrupted = expected.with_columns(pl.lit(0.0).alias(module.FACTOR_COLUMN))
    assert module._pit_audit(corrupted, object(), expected)["all_checks_pass"] is False

    calendar = [date(2024, 1, 1) + timedelta(days=index) for index in range(120)]
    month_ends = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 19)]
    days = module._eligible_rebalance_days(month_ends, calendar, date(2024, 1, 1), date(2024, 3, 31))
    assert days == [date(2024, 1, 31), date(2024, 2, 29)]

    frame = pl.DataFrame({
        "factor": [1.0, 2.0, 3.0, 4.0],
        "forward_return": [0.01, 0.02, 0.03, 0.04],
    })
    assert module._rank_ic(frame, "factor", "forward_return", minimum=4) == 1.0