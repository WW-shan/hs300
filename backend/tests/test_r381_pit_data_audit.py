from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r381_pit_data_audit.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r381_pit_data_audit", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshots(days: list[date], symbols: list[str], update_day: date | None = None) -> pl.DataFrame:
    return pl.DataFrame({
        "query_date": [day for day in days for _ in symbols],
        "update_date": [update_day or day for day in days for _ in symbols],
        "symbol": [symbol for _ in days for symbol in symbols],
    })


def test_membership_audit_does_not_carry_last_snapshot_past_its_date() -> None:
    module = _module()
    first = date(2024, 1, 2)
    second = date(2024, 1, 3)
    missing = date(2024, 1, 4)
    snapshots = _snapshots([first, second], ["000001.SZ", "600000.SH"])

    audit = module.audit_membership_coverage(
        snapshots,
        [first, second, missing],
        expected_count=2,
    )

    assert audit["complete"] is False
    assert audit["missing_dates"] == [missing.isoformat()]
    assert audit["last_snapshot_date"] == second.isoformat()
    assert audit["stale_tail_days"] == 1


def test_membership_audit_counts_duplicate_rows_before_deduplicating() -> None:
    module = _module()
    day = date(2024, 1, 2)
    snapshots = _snapshots([day], ["000001.SZ", "600000.SH"])
    duplicated = pl.concat([snapshots, snapshots.head(1)])

    audit = module.audit_membership_coverage(
        duplicated,
        [day],
        expected_count=2,
    )

    assert audit["duplicate_rows"] == 1
    assert audit["complete"] is False


def test_fetch_missing_snapshots_closes_tail_and_rejects_short_snapshot() -> None:
    module = _module()
    first = date(2024, 1, 2)
    tail = date(2024, 1, 3)
    existing = _snapshots([first], ["000001.SZ", "600000.SH"])
    calls: list[date] = []

    def fetch(day: date) -> list[dict]:
        calls.append(day)
        return [
            {"symbol": "000001.SZ", "name": "A", "update_date": day},
            {"symbol": "600000.SH", "name": "B", "update_date": day},
        ]

    merged, audit = module.fetch_missing_snapshots(
        existing,
        [first, tail],
        fetch,
        expected_count=2,
    )

    assert calls == [tail]
    assert merged.filter(pl.col("query_date") == tail).height == 2
    assert audit["complete"] is True

    def short_fetch(day: date) -> list[dict]:
        del day
        return [{"symbol": "000001.SZ", "name": "A", "update_date": tail}]

    try:
        module.fetch_missing_snapshots(existing, [first, tail], short_fetch, expected_count=2)
    except ValueError as exc:
        assert "expected 2" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("short membership snapshot must fail closed")


def test_industry_asof_never_uses_a_future_effective_classification() -> None:
    module = _module()
    raw = pl.DataFrame({
        "股票代码": ["000001", "000001", "600000"],
        "计入日期": [date(2020, 1, 1), date(2022, 7, 1), date(2020, 1, 1)],
        "更新日期": [date(2020, 1, 1), date(2022, 7, 1), date(2020, 1, 1)],
        "行业代码": ["480101", "480301", "430101"],
    })
    instruments = pl.DataFrame({
        "symbol": ["000001.SZ", "600000.SH"],
        "code": ["000001", "600000"],
    })
    history, _ = module.normalize_industry_history(raw, instruments)
    panel = pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ", "600000.SH"],
        "date": [date(2022, 6, 30), date(2022, 7, 1), date(2022, 7, 1)],
    })

    joined = module.attach_effective_industry(panel, history)

    assert joined.filter(pl.col("date") == date(2022, 6, 30))["industry_pit"][0] == "48"
    assert joined.filter(pl.col("date") == date(2022, 7, 1))["industry_pit"][0] == "48"
    assert joined.filter(pl.col("symbol") == "600000.SH")["industry_pit"][0] == "43"


def test_rebuild_industry_fields_preserves_unrelated_values_and_computes_relative_strength() -> None:
    module = _module()
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(21)]
    closes = {
        "000001.SZ": [100.0 + index for index in range(21)],
        "600000.SH": [100.0 + 2 * index for index in range(21)],
        "600001.SH": [100.0 + 3 * index for index in range(21)],
    }
    existing_rows = []
    price_rows = []
    for symbol, values in closes.items():
        for day, close in zip(days, values, strict=True):
            existing_rows.append({"symbol": symbol, "date": day, "keep_me": symbol})
            price_rows.append({"symbol": symbol, "date": day, "close": close})
    existing = pl.DataFrame(existing_rows)
    prices = pl.DataFrame(price_rows)
    industry = pl.DataFrame({
        "symbol": ["000001.SZ", "600000.SH", "600001.SH"],
        "effective_date": [date(2020, 1, 1)] * 3,
        "industry_pit": ["48", "48", "43"],
    })

    rebuilt, audit = module.rebuild_industry_fields(existing, prices, industry)

    final = rebuilt.filter(pl.col("date") == days[-1]).sort("symbol")
    assert set(final["keep_me"].to_list()) == set(closes)
    ret_a = closes["000001.SZ"][-1] / closes["000001.SZ"][-21] - 1.0
    ret_b = closes["600000.SH"][-1] / closes["600000.SH"][-21] - 1.0
    industry_48 = (ret_a + ret_b) / 2.0
    value = final.filter(pl.col("symbol") == "000001.SZ")["stock_rel_industry_20d"][0]
    assert abs(value - (ret_a - industry_48)) < 1e-12
    assert audit["industry_coverage"] == 1.0


def test_data_integrity_gate_fails_if_industry_coverage_is_below_99_percent() -> None:
    module = _module()
    membership = {"complete": True}
    industry = {"industry_coverage": 0.989999}

    gate = module.evaluate_data_integrity(membership, industry)

    assert gate["membership_complete"] is True
    assert gate["industry_coverage"] is False
    assert gate["all_pass"] is False


def test_minimal_extension_frame_keeps_only_strategy_inputs_and_pit_industry_fields() -> None:
    module = _module()
    day = date(2024, 1, 2)
    existing = pl.DataFrame({
        "symbol": ["000001.SZ"],
        "date": [day],
        "holder_top10_free_float_share": [0.3],
        "equity_risk_premium": [5.8],
        "credit_spread_5y_low": [-0.4],
        "term_spread_10y_2y": [0.7],
        "unused_snapshot_field": [123.0],
        "industry_mom_20d": [-9.0],
        "stock_rel_industry_20d": [-9.0],
    })
    prices = pl.DataFrame({
        "symbol": ["000001.SZ"] * 21,
        "date": [day + timedelta(days=index) for index in range(21)],
        "close": [100.0 + index for index in range(21)],
    })
    industry = pl.DataFrame({
        "symbol": ["000001.SZ"],
        "effective_date": [date(2020, 1, 1)],
        "industry_pit": ["48"],
    })

    frame, audit = module.build_minimal_extension_frame(existing, prices, industry)

    assert frame.columns == [
        "symbol",
        "date",
        "holder_top10_free_float_share",
        "equity_risk_premium",
        "credit_spread_5y_low",
        "term_spread_10y_2y",
        "industry_mom_20d",
        "stock_rel_industry_20d",
    ]
    assert "unused_snapshot_field" not in frame.columns
    assert audit["retained_fields"] == list(module.MINIMAL_EXT_FIELDS)


def test_performance_gates_check_absolute_thresholds_and_r380_sensitivity() -> None:
    module = _module()
    current = {
        "center_2x": {
            "full": {"annual": 0.12, "sharpe": 1.25, "mdd": -0.09},
            "yearly": {"2022": -0.01, "2023": 0.08},
            "recent": {"total_return": 0.04},
            "rolling": {"positive_count": 3},
            "sealed": {"total_return": 0.18, "sharpe": 1.20, "mdd": -0.09},
        },
        "control_2x": {"sealed": {"total_return": 0.16, "sharpe": 1.10, "mdd": -0.10}},
        "center_3x": {"full": {"annual": 0.10, "sharpe": 1.10, "mdd": -0.10}},
    }
    r380 = {
        "center_2x": {"full": {"annual": 0.121, "sharpe": 1.27, "mdd": -0.091}},
        "center_3x": {"full": {"annual": 0.11, "sharpe": 1.12, "mdd": -0.092}},
    }

    result = module.evaluate_performance_gates(current, r380)

    assert result["all_performance_pass"] is True
    assert result["sensitivity_pass"] is True
    assert result["checks"]["2x_annual"] is True
    assert result["checks"]["sealed_beats_control_return"] is True

    current["center_2x"]["full"]["annual"] = 0.10
    failed = module.evaluate_performance_gates(current, r380)
    assert failed["sensitivity_checks"]["2x_annual_delta"] is False
    assert failed["all_pass"] is False


def test_performance_report_stops_before_reading_returns_when_data_gate_fails() -> None:
    module = _module()
    called = False

    def chain_runner() -> dict:
        nonlocal called
        called = True
        raise AssertionError("strategy chain must not run after a data-gate failure")

    input_audit = {
        "data_integrity_pass": False,
        "data_gate": {"all_pass": False},
    }
    try:
        module.build_report(input_audit, chain_runner=chain_runner)
    except module.DataIntegrityGateError as exc:
        assert "data integrity" in str(exc).lower()
    else:  # pragma: no cover - assertion branch
        raise AssertionError("failed data integrity gate must abort report construction")

    assert called is False
