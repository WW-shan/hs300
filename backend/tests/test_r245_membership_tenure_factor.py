from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_r245_membership_tenure_factor.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r245_membership_tenure_factor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_r245_tenure_resets_after_exit_and_reentry() -> None:
    module = _load_builder()
    snapshots = pl.DataFrame({
        "date": [
            date(2018, 1, 1), date(2018, 1, 1),
            date(2020, 1, 2), date(2020, 1, 2),
            date(2021, 1, 4),
            date(2022, 1, 4), date(2022, 1, 4),
        ],
        "symbol": ["A", "B", "A", "C", "B", "A", "B"],
    })
    starts = module._membership_starts(snapshots).sort(["snapshot_date", "symbol"])
    assert starts.filter((pl.col("snapshot_date") == date(2020, 1, 2)) & (pl.col("symbol") == "C"))["start_date"][0] == date(2020, 1, 2)
    assert starts.filter((pl.col("snapshot_date") == date(2022, 1, 4)) & (pl.col("symbol") == "A"))["start_date"][0] == date(2022, 1, 4)

    daily = module._tenure_daily([date(2022, 1, 4), date(2022, 7, 4), date(2023, 1, 4)], snapshots)
    first = daily.filter((pl.col("date") == date(2022, 1, 4)) & (pl.col("symbol") == "A"))[module.FIELD][0]
    later = daily.filter((pl.col("date") == date(2023, 1, 4)) & (pl.col("symbol") == "A"))[module.FIELD][0]
    assert math.isclose(float(first), 1.0, rel_tol=1e-12)
    assert later < first


def test_r245_candidate_and_control_strategies_load() -> None:
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine(strategy_dirs=[ROOT / "data" / "strategies" / "ai"])
    candidate = engine.get("ai_hs300_index_tenure_h20_sl20")
    control = engine.get("ai_hs300_index_tenure_control")

    assert candidate.meta["research_only"] is True
    assert control.meta["research_only"] is True
    assert candidate.meta["scoring"]["ext_alt_hs300_tenure_hs300_membership_tenure_low"] == 0.30
    assert "ext_alt_hs300_tenure_hs300_membership_tenure_low" not in control.meta["scoring"]
    assert candidate.required_features == control.required_features
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert math.isclose(sum(candidate.meta["scoring"].values()), 1.0, rel_tol=1e-12)

    frame = pl.DataFrame({
        "industry_l1": ["银行", "非银金融", "电子", None],
        "ma60": [1.0, 1.0, 1.0, 1.0],
        "ext_alt_hs300_tenure_hs300_membership_tenure_low": [0.1, 0.2, 0.3, 0.4],
    })
    assert frame.filter(candidate.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
    assert frame.filter(control.filter_fn(frame, {}))["industry_l1"].to_list() == ["电子"]
def test_r245_tenure_is_asof_and_never_uses_future_membership() -> None:
    module = _load_builder()
    snapshots = pl.DataFrame({
        "date": [
            date(2018, 1, 1), date(2018, 1, 1),
            date(2020, 1, 2), date(2020, 1, 2),
            date(2021, 1, 4),
            date(2022, 1, 4), date(2022, 1, 4),
        ],
        "symbol": ["A", "B", "A", "C", "B", "A", "B"],
    })
    assert module._tenure_daily([date(2017, 12, 29)], snapshots).height == 0

    daily = module._tenure_daily(
        [date(2020, 1, 2), date(2020, 3, 1), date(2021, 12, 31), date(2022, 1, 4)],
        snapshots,
    )
    first_day = daily.filter(pl.col("date") == date(2020, 1, 2))
    assert set(first_day["symbol"].to_list()) == {"A", "C"}
    assert math.isclose(float(first_day.filter(pl.col("symbol") == "C")[module.FIELD][0]), 1.0)
    assert float(first_day.filter(pl.col("symbol") == "A")[module.FIELD][0]) < 1.0

    carried = daily.filter(pl.col("date") == date(2020, 3, 1))
    assert set(carried["symbol"].to_list()) == {"A", "C"}

    before_reentry = daily.filter(pl.col("date") == date(2021, 12, 31))
    assert before_reentry["symbol"].to_list() == ["B"]

    reentry = daily.filter(pl.col("date") == date(2022, 1, 4))
    assert set(reentry["symbol"].to_list()) == {"A", "B"}
    assert math.isclose(float(reentry.filter(pl.col("symbol") == "A")[module.FIELD][0]), 1.0)


def test_r245_pit_audit_and_prevalence() -> None:
    builder = _load_builder()
    backtest = importlib.util.spec_from_file_location(
        "backtest_r245_membership_tenure",
        ROOT / "scripts" / "backtest_r245_membership_tenure.py",
    )
    module = importlib.util.module_from_spec(backtest)
    assert backtest.loader is not None
    backtest.loader.exec_module(module)

    snapshots = pl.DataFrame({
        "date": [date(2018, 1, 1), date(2018, 1, 1), date(2020, 1, 2)],
        "symbol": ["A", "B", "C"],
    })
    dates = [date(2018, 1, 2), date(2020, 1, 2)]
    factor = builder._tenure_daily(dates, snapshots)
    audit = module._pit_audit(factor, builder, snapshots)
    assert audit["all_checks_pass"] is True
    assert audit["missing_written_rows"] == 0
    assert audit["unexpected_written_rows"] == 0

    corrupted = factor.with_columns(
        pl.when(pl.col("symbol") == "A").then(0.0).otherwise(pl.col(module.FACTOR_COLUMN)).alias(module.FACTOR_COLUMN)
    )
    assert module._pit_audit(corrupted, builder, snapshots)["all_checks_pass"] is False

    rows = []
    counts = [10, 9, 12]
    for offset, count in enumerate(counts):
        day = module.START.fromordinal(module.START.toordinal() + offset)
        for index in range(12):
            rows.append({
                "date": day,
                "symbol": f"{index:06d}.SZ",
                module.FACTOR_COLUMN: 0.6 if index < count else 0.4,
            })
    prevalence = module._tenure_prevalence(pl.DataFrame(rows))
    assert prevalence["observed_dates"] == 3
    assert prevalence["qualifying_dates"] == 2
    assert prevalence["passes_d4"] is False

    all_qualify = pl.DataFrame(rows).with_columns(pl.lit(0.6).alias(module.FACTOR_COLUMN))
    assert module._tenure_prevalence(all_qualify)["passes_d4"] is True


def test_r245_backtest_contract_and_ic_helpers() -> None:
    spec = importlib.util.spec_from_file_location(
        "backtest_r245_membership_tenure",
        ROOT / "scripts" / "backtest_r245_membership_tenure.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.FACTOR_ID == "ext_alt_hs300_tenure_hs300_membership_tenure_low"
    assert module.STRATEGIES == {
        "candidate": "ai_hs300_index_tenure_h20_sl20",
        "control": "ai_hs300_index_tenure_control",
    }
    calendar = [date(2024, 1, 1) + timedelta(days=index) for index in range(120)]
    month_ends = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 19)]
    days = module._eligible_rebalance_days(month_ends, calendar, date(2024, 1, 1), date(2024, 3, 31))
    assert days == [date(2024, 1, 31), date(2024, 2, 29)]

    frame = pl.DataFrame({
        "factor": [1.0, 2.0, 3.0, 4.0],
        "forward_return": [0.01, 0.02, 0.03, 0.04],
    })
    assert module._rank_ic(frame, "factor", "forward_return", minimum=4) == 1.0