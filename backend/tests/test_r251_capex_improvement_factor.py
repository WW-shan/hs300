from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r251_capex_improvement_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r251_capex_improvement_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_financial_fixture(tmp_path: Path) -> None:
    income_dir = tmp_path / "financials" / "income"
    income_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A", "A", "A", "A", "B", "B", "C", "C", "D", "D", "E", "E"],
        "period_end": [
            "2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31",
            "2021-12-31", "2022-12-31",
            "2021-12-31", "2022-12-31",
            "2020-12-31", "2022-12-31",
            "2021-12-31", "2022-12-31",
        ],
        "revenue": [
            100.0, 200.0, 250.0, 200.0,
            100.0, 120.0,
            100.0, 0.0,
            100.0, 130.0,
            100.0, 120.0,
        ],
    }).write_parquet(income_dir / "part.parquet")

    capex_dir = tmp_path / "research" / "r86_intangible"
    capex_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["A", "A", "A", "A", "B", "B", "C", "C", "D", "D", "E", "E"],
        "period_end": [
            date(2020, 12, 31), date(2021, 12, 31), date(2022, 12, 31), date(2023, 12, 31),
            date(2021, 12, 31), date(2022, 12, 31),
            date(2021, 12, 31), date(2022, 12, 31),
            date(2020, 12, 31), date(2022, 12, 31),
            date(2021, 12, 31), date(2022, 12, 31),
        ],
        "notice_date": [
            date(2021, 3, 1), date(2022, 3, 1), date(2023, 6, 10), date(2024, 3, 1),
            date(2022, 3, 1), date(2023, 3, 1),
            date(2022, 3, 1), date(2023, 3, 1),
            date(2021, 3, 1), date(2023, 3, 1),
            date(2022, 3, 1), date(2023, 3, 1),
        ],
        "capex": [
            10.0, 20.0, 20.0, 40.0,
            10.0, None,
            10.0, 12.0,
            10.0, 15.0,
            10.0, float("nan"),
        ],
    }).write_parquet(capex_dir / "A.parquet")


def test_r251_formula_pit_missing_values_and_staleness(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    base = module._load_base_builder()
    _write_financial_fixture(tmp_path)
    monkeypatch.setattr(base, "DATA", tmp_path)

    annual = module._capex_improvement_annual(base).sort("period_end")
    assert annual.height == 3
    assert annual["symbol"].unique().to_list() == ["A"]
    assert annual["effective_date"].to_list() == [
        date(2022, 5, 3), date(2023, 6, 11), date(2024, 5, 3),
    ]
    values = annual[module.FIELD].to_list()
    assert math.isclose(float(values[0]), 0.0, abs_tol=1e-12)
    assert math.isclose(float(values[1]), 0.02, abs_tol=1e-12)
    assert math.isclose(float(values[2]), -0.12, abs_tol=1e-12)

    dates = [
        date(2022, 5, 2), date(2022, 5, 3), date(2022, 6, 1),
        date(2023, 6, 10), date(2023, 6, 11),
        date(2024, 5, 2), date(2024, 5, 3),
        date(2025, 5, 14), date(2025, 5, 15),
    ]
    daily = module._capex_improvement_daily(base, dates, ["A", "B", "C", "D", "E"]).sort(["date", "symbol"])
    a_values = {
        row["date"]: row[module.FIELD]
        for row in daily.filter(pl.col("symbol") == "A").iter_rows(named=True)
    }
    assert a_values[date(2022, 5, 2)] is None
    assert math.isclose(float(a_values[date(2022, 5, 3)]), 0.0, abs_tol=1e-12)
    assert a_values[date(2023, 6, 10)] is None
    assert math.isclose(float(a_values[date(2023, 6, 11)]), 0.02, abs_tol=1e-12)
    assert math.isclose(float(a_values[date(2024, 5, 2)]), 0.02, abs_tol=1e-12)
    assert math.isclose(float(a_values[date(2024, 5, 3)]), -0.12, abs_tol=1e-12)
    assert math.isclose(float(a_values[date(2025, 5, 14)]), -0.12, abs_tol=1e-12)
    assert a_values[date(2025, 5, 15)] is None

    invalid = daily.filter(
        (pl.col("date") == date(2023, 6, 11)) & pl.col("symbol").is_in(["B", "C", "D", "E"])
    )
    assert invalid.height == 4
    assert invalid[module.FIELD].null_count() == 4


def test_r251_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_capex_improvement_quality_h20_sl20")
    control = engine.get("ai_hs300_capex_improvement_quality_control")

    factor_id = "ext_alt_hs300_capexyoy_capex_intensity_improvement_yoy"
    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"][factor_id] == 0.45
    assert factor_id not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        factor_id: [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]


def test_r251_backtest_contract_constants() -> None:
    spec = importlib.util.spec_from_file_location(
        "backtest_r251_capex_improvement",
        ROOT / "scripts" / "backtest_r251_capex_improvement.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.FACTOR_ID == "ext_alt_hs300_capexyoy_capex_intensity_improvement_yoy"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_capex_improvement_quality_h20_sl20",
        "control": "ai_hs300_capex_improvement_quality_control",
    }
    assert module.OUT.name == "r251_capex_improvement.json"
    assert module.CACHE.name == "r251_cache"
    assert date(2023, 5, 4) == module.START
    assert [(date(2023, 5, 4), date(2024, 12, 31)), (date(2025, 1, 2), date(2026, 10, 16))] == module.SEGMENTS
