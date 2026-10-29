from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "build_r272_operating_margin_factor.py"
RUNNER_PATH = ROOT / "scripts" / "backtest_r272_operating_margin_improvement.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_r272_operating_margin_factor_test", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_runner():
    spec = importlib.util.spec_from_file_location("backtest_r272_operating_margin_improvement_test", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quarterly_operating_margin_yoy_is_pit_and_uses_cumulative_differences(
    tmp_path: Path, monkeypatch
) -> None:
    module = _load_builder()
    source = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 6,
            "period_end": [
                "2022-03-31", "2022-06-30", "2022-09-30",
                "2023-03-31", "2023-06-30", "2023-09-30",
            ],
            "announce_date": [
                "2022-04-15", "2022-08-10", "2022-10-20",
                "2023-04-15", "2023-08-10", "2023-10-20",
            ],
            "revenue": [100.0, 230.0, 390.0, 100.0, 260.0, 450.0],
            "operating_profit": [10.0, 25.0, 45.0, 15.0, 39.0, 72.0],
        }
    )
    source_path = tmp_path / "income.parquet"
    source.write_parquet(source_path)
    monkeypatch.setattr(module, "INCOME", source_path)

    records = module._financial_panel()
    q1 = records.filter(pl.col("effective_date") == date(2023, 4, 16)).row(0, named=True)
    q2 = records.filter(pl.col("effective_date") == date(2023, 8, 11)).row(0, named=True)
    q3 = records.filter(pl.col("effective_date") == date(2023, 10, 21)).row(0, named=True)

    assert abs(q1[module.FIELD] - (15.0 / 100.0 - 10.0 / 100.0)) < 1e-12
    assert abs(q2[module.FIELD] - (24.0 / 160.0 - 15.0 / 130.0)) < 1e-12
    assert abs(q3[module.FIELD] - (33.0 / 190.0 - 20.0 / 160.0)) < 1e-12
    assert q2["effective_date"] == date(2023, 8, 11)


def test_quarter_gaps_do_not_create_a_false_quarter_or_yoy_value(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    source = pl.DataFrame(
        {
            "symbol": ["A"] * 5,
            "period_end": ["2022-03-31", "2022-06-30", "2022-12-31", "2023-03-31", "2023-06-30"],
            "announce_date": ["2022-04-15", "2022-08-10", "2023-03-15", "2023-04-15", "2023-08-10"],
            "revenue": [100.0, 210.0, 430.0, 120.0, 250.0],
            "operating_profit": [10.0, 21.0, 43.0, 18.0, 37.5],
        }
    )
    source_path = tmp_path / "income_gap.parquet"
    source.write_parquet(source_path)
    monkeypatch.setattr(module, "INCOME", source_path)

    records = module._financial_panel()
    # The missing Q3 report prevents treating the Q4 cumulative value as a Q4 flow.
    assert records.filter(pl.col("effective_date") == date(2024, 1, 1)).is_empty()
    # Q2 2023 remains valid because both Q2 cumulative reports exist and are consecutive.
    assert records.filter(pl.col("effective_date") == date(2023, 8, 11)).height == 1


def test_financial_panel_is_sorted_after_deduplicating_effective_dates(tmp_path: Path, monkeypatch) -> None:
    module = _load_builder()
    source = pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 12,
            "period_end": [
                "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
                "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
                "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
            ],
            "announce_date": [
                "2021-04-15", "2021-08-10", "2021-10-20", "2022-03-15",
                "2022-04-15", "2022-08-10", "2022-10-20", "2023-03-15",
                "2023-04-15", "2023-08-10", "2023-10-20", "2024-03-15",
            ],
            "revenue": [100.0, 220.0, 360.0, 500.0, 110.0, 240.0, 390.0, 540.0, 120.0, 260.0, 420.0, 590.0],
            "operating_profit": [10.0, 22.0, 36.0, 50.0, 12.0, 25.0, 41.0, 58.0, 15.0, 31.0, 52.0, 72.0],
        }
    )
    source_path = tmp_path / "income_sorted_asof.parquet"
    source.write_parquet(source_path)
    monkeypatch.setattr(module, "INCOME", source_path)

    records = module._financial_panel()

    assert records.height >= 8
    assert records.select(["symbol", "effective_date"]).equals(
        records.sort(["symbol", "effective_date"]).select(["symbol", "effective_date"])
    )
    calendar = pl.DataFrame(
        {"symbol": ["000001.SZ"] * 300, "date": pl.date_range(date(2022, 1, 1), date(2022, 10, 27), interval="1d", eager=True)}
    )
    aligned = calendar.sort(["symbol", "date"]).join_asof(
        records,
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    )
    expected = records.filter(pl.col("effective_date") <= date(2022, 10, 27)).sort("effective_date")["effective_date"][-1]
    assert aligned["effective_date"][-1] == expected


def test_r229_discovery_segment_key_matches_r272_runner() -> None:
    import importlib.util

    r229_path = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
    r112_path = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
    r229_spec = importlib.util.spec_from_file_location("r229_r272_contract_test", r229_path)
    r112_spec = importlib.util.spec_from_file_location("r112_r272_contract_test", r112_path)
    assert r229_spec is not None and r229_spec.loader is not None
    assert r112_spec is not None and r112_spec.loader is not None
    r229 = importlib.util.module_from_spec(r229_spec)
    r112 = importlib.util.module_from_spec(r112_spec)
    r229_spec.loader.exec_module(r229)
    r112_spec.loader.exec_module(r112)

    dates = []
    current = date(2022, 7, 1)
    while current <= date(2026, 10, 16):
        dates.append(current)
        current += timedelta(days=1)
    curve = pl.DataFrame({"date": dates, "value": [1.0 + i * 0.0001 for i in range(len(dates))]})

    result = r229._result(r112, curve)

    assert result["discovery_2022_2024"]["days"] > 0
    assert result["validation_2025_2026"]["days"] > 0


def test_pit_audit_compares_only_matching_historical_member_keys() -> None:
    runner = _load_runner()
    days = [date(2023, 5, 4), date(2023, 5, 5)]
    expected = pl.DataFrame(
        {
            "symbol": ["A", "B", "A", "B"],
            "date": [days[0], days[0], days[1], days[1]],
            runner.FACTOR_COLUMN: [0.1, 0.2, 0.15, 0.25],
        }
    )
    membership = pl.DataFrame(
        {
            "effective_date": [days[0], days[0]],
            "symbol": ["A", "B"],
        }
    )
    actual = expected.vstack(
        pl.DataFrame(
            {
                "symbol": ["OUTSIDE_HS300"],
                "date": [days[0]],
                runner.FACTOR_COLUMN: [9.0],
            }
        )
    )

    class Builder:
        @staticmethod
        def _factor_daily(_base_builder, _dates, _symbols):
            return expected

    audit = runner._pit_audit(actual, Builder(), None, days, ["A", "B"], membership)

    assert audit["historical_member_keys"] == 4
    assert audit["rows_expected_on_member_keys"] == 4
    assert audit["rows_written_on_member_keys"] == 4
    assert audit["row_keys_extra_in_written"] == 0
    assert audit["max_absolute_value_difference"] == 0.0
    assert audit["all_checks_pass"] is True


def test_pit_audit_fails_when_a_historical_member_key_is_missing() -> None:
    runner = _load_runner()
    days = [date(2023, 5, 4), date(2023, 5, 5)]
    expected = pl.DataFrame(
        {
            "symbol": ["A", "B", "A", "B"],
            "date": [days[0], days[0], days[1], days[1]],
            runner.FACTOR_COLUMN: [0.1, 0.2, 0.15, 0.25],
        }
    )
    membership = pl.DataFrame(
        {"effective_date": [days[0], days[0]], "symbol": ["A", "B"]}
    )
    actual = expected.filter(~((pl.col("symbol") == "B") & (pl.col("date") == days[1])) )

    class Builder:
        @staticmethod
        def _factor_daily(_base_builder, _dates, _symbols):
            return expected

    audit = runner._pit_audit(actual, Builder(), None, days, ["A", "B"], membership)

    assert audit["row_keys_missing_from_written"] == 1
    assert audit["all_checks_pass"] is False
