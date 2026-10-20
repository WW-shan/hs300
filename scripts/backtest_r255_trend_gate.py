#!/usr/bin/env python3
"""R255: system-engine test of an HS300 200-day trend entry gate on R253 quality."""

from __future__ import annotations

import gc
import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r255_trend_gate.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R166_SCRIPT = ROOT / "scripts" / "backtest_r166_weak_regime_factor_scan.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R253_SCRIPT = ROOT / "scripts" / "backtest_r253_min_rank.py"
R255_BUILDER = ROOT / "scripts" / "build_r255_trend_gate_factor.py"
CACHE = DATA / "research" / "r255_cache"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
WINDOW = 252
GATE_WINDOW = 200
MATCHING = "open_t+1"
SEGMENTS = [
    (date(2023, 5, 4), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_minrank_ma200_gate_quality_h20_sl20",
    "control": "ai_hs300_minrank_ma200_gate_quality_control",
}
FACTOR_ID = "ext_alt_market_regime_hs300_above_ma200"
FACTOR_COLUMN = "hs300_above_ma200"
GATE_CONFIG_ID = "alt_market_regime"
QUALITY_FACTOR_ID = "ext_alt_hs300_minrank_management_ocf_gm_min_rank"
QUALITY_COLUMN = "management_ocf_gm_min_rank"
QUALITY_CONFIG_ID = "alt_hs300_minrank"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _read_ext_frame(config_id: str, columns: list[str]) -> pl.DataFrame:
    root = DATA / "ext_data" / config_id / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"extension data is missing: {root}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(columns)
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
        .sort(["date", "symbol"])
    )


def _gate_rows() -> pl.DataFrame:
    return _read_ext_frame(GATE_CONFIG_ID, ["symbol", "date", FACTOR_COLUMN])


def _quality_rows() -> pl.DataFrame:
    return _read_ext_frame(QUALITY_CONFIG_ID, ["symbol", "date", QUALITY_COLUMN])


def _index_frame() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("index daily data is missing")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .filter(pl.col("symbol") == "000300.SH")
        .select([pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64)])
        .collect()
        .unique(subset=["date"], keep="last")
        .sort("date")
    )


def _gate_date_values(gate_all: pl.DataFrame) -> pl.DataFrame:
    return (
        gate_all.filter(pl.col(FACTOR_COLUMN).is_not_null())
        .group_by("date")
        .agg(pl.col(FACTOR_COLUMN).min().alias(FACTOR_COLUMN))
        .sort("date")
    )


def _sample_stats(calendar: list[date], gate_by_date: pl.DataFrame, start: date, end: date) -> dict:
    dates = [day for day in calendar if start <= day <= end]
    joined = pl.DataFrame({"date": dates}, schema={"date": pl.Date}).join(
        gate_by_date,
        on="date",
        how="left",
    )
    valid = joined.filter(pl.col(FACTOR_COLUMN).is_not_null())
    return {
        "expected_dates": len(dates),
        "valid_dates": valid.height,
        "coverage": round(valid.height / len(dates), 6) if dates else 0.0,
        "on_dates": int((valid[FACTOR_COLUMN] == 1).sum()),
        "off_dates": int((valid[FACTOR_COLUMN] == 0).sum()),
        "first_valid_date": str(valid["date"].min()) if valid.height else None,
        "last_valid_date": str(valid["date"].max()) if valid.height else None,
    }


def _gate_audit(gate_all: pl.DataFrame, builder, calendar: list[date]) -> dict:
    valid = gate_all.filter(pl.col(FACTOR_COLUMN).is_not_null())
    by_date = valid.group_by("date").agg(
        pl.col(FACTOR_COLUMN).min().alias("min_value"),
        pl.col(FACTOR_COLUMN).max().alias("max_value"),
        pl.len().alias("rows"),
    )
    date_constant = bool(by_date.select((pl.col("min_value") == pl.col("max_value")).all()).item())
    binary = bool(valid.select(pl.col(FACTOR_COLUMN).is_in([0.0, 1.0]).all()).item())
    duplicate_rows = gate_all.height - gate_all.select(["date", "symbol"]).unique().height
    gate_by_date = _gate_date_values(gate_all)

    samples = {
        "full": _sample_stats(calendar, gate_by_date, START, END),
        "discovery": _sample_stats(calendar, gate_by_date, *SEGMENTS[0]),
        "validation": _sample_stats(calendar, gate_by_date, *SEGMENTS[1]),
        "recent_12m": _sample_stats(calendar, gate_by_date, calendar[-WINDOW], END),
    }

    expected_frame = builder._factor_daily(DATA).select(["symbol", "date", FACTOR_COLUMN])
    actual = gate_all.select(["symbol", "date", FACTOR_COLUMN]).with_columns(
        pl.lit(1, dtype=pl.Int8).alias("_actual_present")
    )
    expected = expected_frame.with_columns(pl.lit(1, dtype=pl.Int8).alias("_expected_present"))
    compared = expected.join(actual, on=["symbol", "date"], how="full", suffix="_actual")
    actual_column = f"{FACTOR_COLUMN}_actual"
    missing_actual = int(compared["_actual_present"].is_null().sum())
    extra_actual = int(compared["_expected_present"].is_null().sum())
    null_mismatch = compared.filter(
        (pl.col(FACTOR_COLUMN).is_null() & pl.col(actual_column).is_not_null())
        | (pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_column).is_null())
    ).height
    both_present = pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_column).is_not_null()
    difference = (
        compared.filter(both_present)
        .select((pl.col(FACTOR_COLUMN) - pl.col(actual_column)).abs().max())
        .item()
    )
    max_abs_difference = float(difference) if difference is not None else None
    value_mismatch = (
        null_mismatch
        + compared.filter(
            both_present & ((pl.col(FACTOR_COLUMN) - pl.col(actual_column)).abs() > 1e-12)
        ).height
    )

    index = _index_frame()
    actual_by_date = (
        valid.group_by("date")
        .agg(pl.col(FACTOR_COLUMN).min().alias("actual"))
        .filter(pl.col("date") >= START)
        .sort("date")
    )
    expected_by_date = (
        builder._build_gate(index)
        .filter((pl.col("date") >= START) & pl.col(FACTOR_COLUMN).is_not_null())
        .rename({FACTOR_COLUMN: "expected"})
        .sort("date")
    )
    formula_compared = expected_by_date.join(actual_by_date, on="date", how="full")
    formula_missing = int(
        formula_compared.filter(
            pl.col("actual").is_null() & pl.col("expected").is_not_null()
        ).height
    )
    formula_extra = int(
        formula_compared.filter(
            pl.col("actual").is_not_null() & pl.col("expected").is_null()
        ).height
    )
    formula_null_mismatch = formula_compared.filter(
        (pl.col("actual").is_null() & pl.col("expected").is_not_null())
        | (pl.col("actual").is_not_null() & pl.col("expected").is_null())
    ).height
    formula_difference = (
        formula_compared.filter(pl.col("actual").is_not_null() & pl.col("expected").is_not_null())
        .select((pl.col("actual") - pl.col("expected")).abs().max())
        .item()
    )
    formula_max_difference = float(formula_difference) if formula_difference is not None else None

    prefix_days = calendar[:: max(1, len(calendar) // 24)]
    if calendar[-1] not in prefix_days:
        prefix_days.append(calendar[-1])
    expected_lookup = {
        row["date"]: row["expected"]
        for row in formula_compared.filter(pl.col("expected").is_not_null()).iter_rows(named=True)
    }
    prefix_mismatches = []
    for day in prefix_days:
        prefix_value = (
            builder._build_gate(index.filter(pl.col("date") <= day))
            .filter(pl.col("date") == day)[FACTOR_COLUMN]
            .item()
        )
        if prefix_value != expected_lookup.get(day):
            prefix_mismatches.append(str(day))

    formula_match = (
        formula_missing == 0
        and formula_extra == 0
        and formula_null_mismatch == 0
        and formula_max_difference is not None
        and formula_max_difference <= 1e-12
    )
    prefix_invariance = not prefix_mismatches
    written_rows_match = missing_actual == 0 and extra_actual == 0 and value_mismatch == 0
    complete_from_start = samples["full"]["coverage"] == 1.0
    no_future = formula_match and prefix_invariance

    return {
        "samples": samples,
        "date_constant": date_constant,
        "binary": binary,
        "duplicate_date_symbol_rows": duplicate_rows,
        "factor_rows_checked": gate_all.height,
        "valid_factor_rows": valid.height,
        "written_rows_match_expected_asof_panel": written_rows_match,
        "missing_written_rows": missing_actual,
        "unexpected_written_rows": extra_actual,
        "value_mismatch_rows": value_mismatch,
        "max_abs_formula_difference": max_abs_difference,
        "formula_audit": {
            "index_symbol": "000300.SH",
            "rolling_window": builder.WINDOW,
            "close_only": index.columns == ["date", "close"],
            "expected_dates": expected_by_date.height,
            "actual_dates": actual_by_date.height,
            "missing_formula_dates": formula_missing,
            "extra_formula_dates": formula_extra,
            "null_mismatch_dates": formula_null_mismatch,
            "max_abs_difference": formula_max_difference,
            "formula_match": formula_match,
            "prefix_invariance_checks": len(prefix_days),
            "prefix_invariance_mismatches": prefix_mismatches,
            "no_future_index_leakage": prefix_invariance,
        },
        "no_future_index_leakage": no_future,
        "complete_from_start": complete_from_start,
        "pit_unit_test": "backend/tests/test_r255_trend_gate_factor.py::test_r255_gate_warmup_equal_close_and_above",
        "all_checks_pass": (
            all(sample["coverage"] >= 0.99 for sample in samples.values())
            and date_constant
            and binary
            and duplicate_rows == 0
            and written_rows_match
            and complete_from_start
            and no_future
        ),
    }


def _factor_ic(
    quality_all: pl.DataFrame,
    gate_by_date: pl.DataFrame,
    r166,
    membership: pl.DataFrame,
    calendar: list[date],
    r253_helper,
) -> dict:
    all_month_ends = [day for day in r166._month_ends() if START <= day <= END]
    forward = r166._forward_returns(calendar, all_month_ends)
    gate_map = gate_by_date.rename({"date": "rebalance_date"})
    panel = (
        forward.join(
            quality_all.rename({"date": "rebalance_date"}),
            on=["rebalance_date", "symbol"],
            how="inner",
        )
        .join(gate_map, on="rebalance_date", how="left")
        .sort(["rebalance_date", "symbol"])
    )
    membership_by_day = r253_helper._month_end_membership(membership, all_month_ends)
    gate_on_days = set(gate_by_date.filter(pl.col(FACTOR_COLUMN) == 1)["date"].to_list())
    out = {}
    for name, (start, end) in {
        "discovery": SEGMENTS[0],
        "validation": SEGMENTS[1],
    }.items():
        allowed_days = r253_helper._eligible_rebalance_days(all_month_ends, calendar, start, end)
        gate_days = [day for day in allowed_days if day in gate_on_days]
        monthly = []
        for day in gate_days:
            rows = panel.filter(pl.col("rebalance_date") == day)
            symbols = membership_by_day.get(day, set())
            part = rows.filter(pl.col("symbol").is_in(symbols))
            ic = r253_helper._rank_ic(part, QUALITY_COLUMN, "forward_return")
            if ic is not None:
                monthly.append({"date": str(day), "ic": round(ic, 6), "names": part.height})
        values = [row["ic"] for row in monthly]
        out[name] = {
            "eligible_month_ends": len(allowed_days),
            "gate_on_month_ends": len(gate_days),
            "months": len(values),
            "mean_ic": round(sum(values) / len(values), 6) if values else None,
            "positive_rate": round(sum(value > 0 for value in values) / len(values), 4)
            if values
            else None,
            "monthly": monthly,
        }
    return out


def _segmented_results(
    service,
    membership,
    r112,
    r229,
    sid: str,
    cost: float,
    r253_helper,
) -> tuple[dict, list[dict]]:
    curves = []
    runs = []
    trades = []
    r253_helper.CACHE = CACHE
    for start, end in SEGMENTS:
        curve, info, segment_trades = r253_helper._run_segment(
            service,
            membership,
            r112,
            r229,
            sid,
            start,
            end,
            cost,
        )
        curves.append(curve)
        runs.append({"start": str(start), "end": str(end), **info})
        trades.extend(segment_trades)
        print(f"{sid} {cost:g}x {start}~{end}: {info['curve_stats']}", flush=True)
        gc.collect()
    stitched = r229._stitch(curves)
    result = r229._result(r112, stitched)
    result["discovery_2023_2024"] = result.pop("discovery_2022_2024")
    return {"runs": runs, "result": result}, trades


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r166 = _load(R166_SCRIPT, "r166")
    r221 = _load(R221_SCRIPT, "r221")
    r229 = _load(R229_SCRIPT, "r229")
    r239 = _load(R239_SCRIPT, "r239")
    builder = _load(R255_BUILDER, "r255_builder")
    r253 = _load(R253_SCRIPT, "r253")

    calendar = r229._benchmark_dates(START, END)
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    universe = r221._universe_audit(all_members)
    service = r112._service()

    gate_all = _gate_rows()
    gate_by_date = _gate_date_values(gate_all)
    gate_audit = _gate_audit(gate_all, builder, calendar)
    quality_all = _quality_rows()
    factor_ic = _factor_ic(quality_all, gate_by_date, r166, membership, calendar, r253)

    results = {}
    trades = {}
    for name, sid in STRATEGIES.items():
        result, run_trades = _segmented_results(service, membership, r112, r229, sid, 3.0, r253)
        results[name] = result
        trades[name] = run_trades

    stress = {}
    for cost in (4.0, 5.0):
        result, _ = _segmented_results(
            service, membership, r112, r229, STRATEGIES["candidate"], cost, r253
        )
        stress[f"{cost:g}x"] = result

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    candidate_trades = trades["candidate"]
    concentration_full = r239._aggregate(candidate_trades, industry)
    validation_trades = [
        row for row in candidate_trades if row["segment"].startswith("2025-01-02~")
    ]
    concentration_validation = r239._aggregate(validation_trades, industry)
    validation_positive = sum(
        1 for row in concentration_validation["per_symbol"] if row["pnl_amount"] > 0
    )

    candidate = results["candidate"]["result"]
    control = results["control"]["result"]
    samples = gate_audit["samples"]
    d1 = all(sample["coverage"] >= 0.99 for sample in samples.values())
    d2 = all(
        samples[name]["on_dates"] >= 30 and samples[name]["off_dates"] >= 30
        for name in ("full", "discovery", "validation")
    )
    formula = gate_audit["formula_audit"]
    d3 = (
        formula["rolling_window"] == GATE_WINDOW
        and formula["close_only"]
        and formula["formula_match"]
        and MATCHING == "open_t+1"
    )
    d4 = (
        gate_audit["date_constant"]
        and gate_audit["binary"]
        and gate_audit["complete_from_start"]
        and gate_audit["no_future_index_leakage"]
        and gate_audit["written_rows_match_expected_asof_panel"]
        and gate_audit["duplicate_date_symbol_rows"] == 0
    )
    i1 = (
        factor_ic["discovery"]["months"] >= 6 and (factor_ic["discovery"]["mean_ic"] or 0.0) >= 0.02
    )
    i2 = (
        factor_ic["validation"]["months"] >= 12
        and (factor_ic["validation"]["mean_ic"] or 0.0) >= 0.01
        and (factor_ic["validation"]["mean_ic"] or 0.0) > 0
    )
    g1 = (
        candidate["discovery_2023_2024"]["total_return"]
        >= control["discovery_2023_2024"]["total_return"] + 0.05
    )
    g2 = candidate["discovery_2023_2024"]["mdd"] >= control["discovery_2023_2024"]["mdd"] - 0.05
    g3 = (
        candidate["validation_2025_2026"]["total_return"]
        >= control["validation_2025_2026"]["total_return"]
    )
    g4 = (
        candidate["full"]["total_return"] > 0
        and candidate["full"]["total_return"] >= control["full"]["total_return"]
    )
    g5 = (
        candidate["full"]["mdd"] >= -0.40
        and candidate["full"]["mdd"] >= control["full"]["mdd"] - 0.05
    )
    g6 = (
        candidate["rolling_1y"]["positive_rate"] >= 0.60
        and candidate["rolling_1y"]["positive_rate"]
        >= control["rolling_1y"]["positive_rate"] - 0.02
    )
    g7 = (
        candidate["recent_12m"]["total_return"] > 0
        and candidate["recent_12m"]["mdd"] >= control["recent_12m"]["mdd"] - 0.05
    )
    g8 = stress["4x"]["result"]["full"]["total_return"] > 0
    g9 = stress["5x"]["result"]["full"]["total_return"] > 0
    c1 = concentration_full["distinct_symbols"] >= 30
    c2 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top1_net_share"] <= 0.25
    c3 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top3_net_share"] <= 0.55
    c4 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top5_net_share"] <= 0.75
    c5 = validation_positive >= 8
    c6 = (
        concentration_full["top_industry_gross_share"] is not None
        and concentration_full["top_industry_gross_share"] <= 0.45
    )
    c7 = (
        concentration_full["max_symbol_loss_to_gross"] is not None
        and concentration_full["max_symbol_loss_to_gross"] <= 0.20
    )
    decision = {
        "D1": d1,
        "D2": d2,
        "D3": d3,
        "D4": d4,
        "I1": i1,
        "I2": i2,
        "G1": g1,
        "G2": g2,
        "G3": g3,
        "G4": g4,
        "G5": g5,
        "G6": g6,
        "G7": g7,
        "G8": g8,
        "G9": g9,
        "C1": c1,
        "C2": c2,
        "C3": c3,
        "C4": c4,
        "C5": c5,
        "C6": c6,
        "C7": c7,
    }
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": (
            "R255 HS300 200-day trend entry gate on the unchanged R253 nonfinancial "
            "weakest-link quality factor versus a matched quality-cash control"
        ),
        "preregistration": (EVIDENCE / "r255_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "trading_days": len(calendar),
        "universe": universe,
        "factor": {
            "id": FACTOR_ID,
            "config_id": GATE_CONFIG_ID,
            "column": FACTOR_COLUMN,
            "direction": "gate",
            "formula": (
                "hs300_above_ma200 = 1 if 000300.SH close_t > "
                "mean(close_{t-199}...close_t), else 0; null until 200 closes exist"
            ),
            "pit_rule": "close and its trailing 200-trading-day mean are observable at t; execution is T+1 open",
            "missing_data_rule": "exact-date join only; a missing gate date blocks new entries fail-closed",
            "samples": samples,
            "audit": gate_audit,
        },
        "quality_factor": {
            "id": QUALITY_FACTOR_ID,
            "column": QUALITY_COLUMN,
            "formula": (
                "management_ocf_gm_min_rank = min(rank_pct(management_expense_ratio_4q_low), "
                "rank_pct(ocf_margin_4q), rank_pct(gross_margin_yoy_improvement_q))"
            ),
            "role": "unchanged R253 cross-sectional ranking input, tested only on gate-on month ends",
            "conditional_ic": factor_ic,
        },
        "samples": {
            "discovery": ["2023-05-04", "2024-12-31"],
            "validation": ["2025-01-02", str(END)],
            "rolling_1y": "252-trading-day rolling compounded return over the stitched 2023-2026 curve",
            "recent_12m": "last 252 trading-day returns of the stitched 2023-2026 curve",
        },
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": MATCHING,
            "gate_scope": "new entries only; existing positions remain subject to holding period and stop loss",
            "cost_3x": "6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "cost_stress": "4x and 5x scale all four components proportionally",
            "max_positions": 10,
            "score_limit": 30,
            "position_sizing": "equal",
            "holding_days": 20,
            "stop_loss": -0.2,
            "segments": [[str(start), str(end)] for start, end in SEGMENTS],
            "stitching": "two segment returns compounded into one curve; first day of each later segment skipped",
        },
        "strategies": STRATEGIES,
        "results": results,
        "stress": stress,
        "concentration": {
            "full": {
                key: value for key, value in concentration_full.items() if key != "per_symbol"
            },
            "validation": {
                key: value for key, value in concentration_validation.items() if key != "per_symbol"
            },
            "validation_positive_symbols": validation_positive,
            "per_symbol_full": concentration_full["per_symbol"],
        },
        "decision": decision,
        "caveats": [
            "The gate blocks new entries only and cannot prevent losses on positions already open.",
            "The 200-day rule is a fixed prior rather than an optimized parameter and may whipsaw near the average.",
            "The gate is date-constant and has no cross-sectional alpha interpretation.",
            "Conditional discovery IC has only several gate-on month ends and therefore low statistical power.",
            "The underlying R253 quality factor retains financial-snapshot, current-industry and concentration risks.",
            "This is another attempt in the same quality family, so multiple-comparison risk remains.",
            "The full curve is stitched from two system-engine segments; segment boundaries can reset an open position.",
            "Trade P&L shares are contribution diagnostics from two segments each starting at 1,000,000 notional.",
            "Passing every gate would justify paper-trading validation, not production deployment.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "gate": samples,
                "gate_audit": gate_audit["formula_audit"],
                "factor_ic": factor_ic,
                "results": results,
                "stress": {key: value["result"]["full"] for key, value in stress.items()},
                "concentration": payload["concentration"]["full"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
