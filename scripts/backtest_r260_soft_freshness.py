#!/usr/bin/env python3
"""R260: system-engine test of the bounded soft-freshness penalty on R257."""

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
EVIDENCE = (
    DATA
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
)
OUT = EVIDENCE / "r260_soft_freshness.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R166_SCRIPT = ROOT / "scripts" / "backtest_r166_weak_regime_factor_scan.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R253_SCRIPT = ROOT / "scripts" / "backtest_r253_min_rank.py"
R260_BUILDER = ROOT / "scripts" / "build_r260_soft_freshness_factor.py"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
CACHE = DATA / "research" / "r260_cache"
ROUND = "R260"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
WINDOW = 252
SEGMENTS = [
    (date(2023, 5, 4), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_qrs_soft_fresh_h20_sl20",
    "control": "ai_hs300_qrs_soft_fresh_control",
}
FACTOR_ID = "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank"
FACTOR_COLUMN = "quality_rs_soft_fresh_rank"
FACTOR_CONFIG = "alt_hs300_qrsrf"
R257_FACTOR_ID = "ext_alt_hs300_qrs_quality_relative_strength_min_rank"
R257_COLUMN = "quality_relative_strength_min_rank"
R257_CONFIG = "alt_hs300_qrs"
PENALTY_LOOKBACK = 20
PENALTY_CAP = 0.20


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


def _factor_rows() -> pl.DataFrame:
    return _read_ext_frame(FACTOR_CONFIG, ["symbol", "date", FACTOR_COLUMN])


def _r257_rows() -> pl.DataFrame:
    return _read_ext_frame(R257_CONFIG, ["symbol", "date", R257_COLUMN])


def _member_daily(dates: list[date], membership: pl.DataFrame) -> pl.DataFrame:
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")
    return (
        pl.DataFrame({"date": dates}, schema={"date": pl.Date})
        .sort("date")
        .join_asof(
            snapshot_dates,
            left_on="date",
            right_on="effective_date",
            strategy="backward",
        )
        .join(membership, on="effective_date", how="inner")
        .select(["date", "symbol"])
        .unique()
        .sort(["date", "symbol"])
    )


def _coverage(membership: pl.DataFrame, factor_all: pl.DataFrame) -> dict:
    first_valid = factor_all.filter(pl.col(FACTOR_COLUMN).is_not_null())["date"].min()
    factor = factor_all.filter((pl.col("date") >= START) & (pl.col("date") <= END))
    dates = factor.select("date").unique().sort("date")["date"].to_list()
    eligible = _member_daily(dates, membership).join(
        factor,
        on=["date", "symbol"],
        how="left",
    )
    recent_dates = dates[-WINDOW:]

    def sample_stats(start: date, end: date) -> dict:
        rows = eligible.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        valid = rows.filter(pl.col(FACTOR_COLUMN).is_not_null())
        return {
            "eligible_member_rows": rows.height,
            "valid_member_rows": valid.height,
            "coverage": round(valid.height / rows.height, 6) if rows.height else 0.0,
            "eligible_dates": rows["date"].n_unique(),
            "valid_dates": valid["date"].n_unique(),
            "first_valid_date": str(valid["date"].min()) if valid.height else None,
            "last_valid_date": str(valid["date"].max()) if valid.height else None,
            "valid_symbols": valid["symbol"].n_unique() if valid.height else 0,
        }

    full = sample_stats(START, END)
    recent = sample_stats(recent_dates[0], END)
    valid = eligible.filter(pl.col(FACTOR_COLUMN).is_not_null())
    return {
        "full": full,
        "discovery": sample_stats(*SEGMENTS[0]),
        "validation": sample_stats(*SEGMENTS[1]),
        "recent_12m": recent,
        "coverage_2023_2026": full["coverage"],
        "recent_12m_coverage": recent["coverage"],
        "first_valid_date": str(first_valid),
        "factor_min": round(float(valid[FACTOR_COLUMN].min()), 6),
        "factor_p01": round(float(valid[FACTOR_COLUMN].quantile(0.01)), 6),
        "factor_median": round(float(valid[FACTOR_COLUMN].median()), 6),
        "factor_p99": round(float(valid[FACTOR_COLUMN].quantile(0.99)), 6),
        "factor_max": round(float(valid[FACTOR_COLUMN].max()), 6),
        "zero_rate": round(float((valid[FACTOR_COLUMN] == 0.0).mean()), 6),
    }


def _pit_audit(factor_all: pl.DataFrame, builder, base) -> dict:
    expected_frame = builder._factor_daily(base).select(
        ["symbol", "date", FACTOR_COLUMN]
    )
    actual = factor_all.select(["symbol", "date", FACTOR_COLUMN]).with_columns(
        pl.lit(1, dtype=pl.Int8).alias("_actual_present")
    )
    expected = expected_frame.with_columns(
        pl.lit(1, dtype=pl.Int8).alias("_expected_present")
    )
    compared = expected.join(
        actual, on=["symbol", "date"], how="full", suffix="_actual"
    )
    actual_column = f"{FACTOR_COLUMN}_actual"
    missing_actual = int(compared["_actual_present"].is_null().sum())
    extra_actual = int(compared["_expected_present"].is_null().sum())
    null_mismatch = compared.filter(
        (pl.col(FACTOR_COLUMN).is_null() & pl.col(actual_column).is_not_null())
        | (pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_column).is_null())
    ).height
    both_present = (
        pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_column).is_not_null()
    )
    difference = (
        compared.filter(both_present)
        .select((pl.col(FACTOR_COLUMN) - pl.col(actual_column)).abs().max())
        .item()
    )
    max_abs_difference = float(difference) if difference is not None else None
    value_mismatch = (
        null_mismatch
        + compared.filter(
            both_present
            & ((pl.col(FACTOR_COLUMN) - pl.col(actual_column)).abs() > 1e-12)
        ).height
    )
    valid = expected_frame.filter(pl.col(FACTOR_COLUMN).is_not_null())
    factor_min = float(valid[FACTOR_COLUMN].min()) if valid.height else None
    factor_max = float(valid[FACTOR_COLUMN].max()) if valid.height else None
    duplicate_rows = actual.height - actual.select(["date", "symbol"]).unique().height
    range_valid = (
        factor_min is not None
        and factor_max is not None
        and factor_min >= 0.0
        and factor_max <= 1.0
        and bool(valid.select(pl.col(FACTOR_COLUMN).is_finite().all()).item())
    )
    formula_match = (
        max_abs_difference is not None
        and max_abs_difference <= 1e-12
        and value_mismatch == 0
        and missing_actual == 0
        and extra_actual == 0
    )
    return {
        "rule": (
            "rank_pct(R257) multiplied by a bounded recency penalty based on prior R257 "
            "Top30 count within historical nonfinancial HS300 members; R257 value required"
        ),
        "rank_normalization": "rank_pct = (number_of_names - descending_average_rank) / (number_of_names - 1); 1 is best and 0 is worst",
        "factor_rows_checked": actual.height,
        "expected_rows": expected_frame.height,
        "missing_written_rows": missing_actual,
        "unexpected_written_rows": extra_actual,
        "value_mismatch_rows": value_mismatch,
        "max_abs_formula_difference": max_abs_difference,
        "duplicate_date_symbol_rows": duplicate_rows,
        "written_rows_match_expected_asof_panel": missing_actual == 0
        and extra_actual == 0,
        "written_values_match_formula": formula_match,
        "valid_range": [factor_min, factor_max],
        "range_valid": range_valid,
        "pit_unit_test": "backend/tests/test_r260_soft_freshness_factor.py::test_r260_soft_freshness_counts_prior_top30_and_caps_penalty",
        "all_checks_pass": formula_match and range_valid and duplicate_rows == 0,
    }


def _increment_audit(
    factor_all: pl.DataFrame,
    r257_all: pl.DataFrame,
    membership: pl.DataFrame,
    r166,
) -> dict:
    calendar = (
        factor_all.filter((pl.col("date") >= START) & (pl.col("date") <= END))["date"]
        .unique()
        .sort()
        .to_list()
    )
    month_ends = [day for day in r166._month_ends() if START <= day <= END]
    eligible = (
        _member_daily(calendar, membership)
        .join(r257_all, on=["date", "symbol"], how="left")
        .join(
            factor_all.select(["date", "symbol", FACTOR_COLUMN]),
            on=["date", "symbol"],
            how="left",
        )
        .sort(["date", "symbol"])
    )
    valid = eligible.filter(
        pl.col(R257_COLUMN).is_not_null() & pl.col(FACTOR_COLUMN).is_not_null()
    )
    ranked = (
        valid.with_columns(
            pl.col(R257_COLUMN)
            .rank("average", descending=True)
            .over("date")
            .cast(pl.Float64)
            .alias("_rank")
        )
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_names"))
        .filter(pl.col("_names") > 1)
        .with_columns(
            ((pl.col("_names") - pl.col("_rank")) / (pl.col("_names") - 1.0)).alias(
                "_r257_pct"
            )
        )
        .with_columns((pl.col("_r257_pct") - pl.col(FACTOR_COLUMN)).alias("_reduction"))
    )
    r257_top = (
        ranked.sort(["date", R257_COLUMN, "symbol"], descending=[False, True, False])
        .with_columns(
            pl.int_range(1, pl.len() + 1).over("date").cast(pl.Int64).alias("_rank_top")
        )
        .filter(pl.col("_rank_top") <= 30)
        .select(["date", "symbol", pl.lit(1, dtype=pl.Int8).alias("_r257_top")])
    )
    factor_top = (
        ranked.sort(["date", FACTOR_COLUMN, "symbol"], descending=[False, True, False])
        .with_columns(
            pl.int_range(1, pl.len() + 1).over("date").cast(pl.Int64).alias("_rank_top")
        )
        .filter(pl.col("_rank_top") <= 30)
        .select(["date", "symbol", pl.lit(1, dtype=pl.Int8).alias("_factor_top")])
    )
    overlap = (
        r257_top.join(factor_top, on=["date", "symbol"], how="inner")
        .group_by("date")
        .agg(pl.len().cast(pl.Float64).alias("_overlap"))
        .with_columns((pl.col("_overlap") / 30.0).alias("_overlap"))
        .sort("date")
    )
    reduction = ranked["_reduction"]
    mean_reduction = float(reduction.mean()) if reduction.len() else None
    share_reduced = float((reduction > 0).mean()) if reduction.len() else None
    max_reduction = float(reduction.max()) if reduction.len() else None
    overlap_values = overlap["_overlap"]
    overlap_median = float(overlap_values.median()) if overlap_values.len() else None
    share_days_below_one = (
        float((overlap_values < 1.0).mean()) if overlap_values.len() else None
    )
    monthly = []
    for day in month_ends:
        part = valid.filter(pl.col("date") == day)
        if part.height < 30:
            continue
        ranked_part = part.select(
            pl.col(R257_COLUMN).rank("average").alias("x"),
            pl.col(FACTOR_COLUMN).rank("average").alias("y"),
        )
        if ranked_part["x"].n_unique() < 2 or ranked_part["y"].n_unique() < 2:
            continue
        corr = ranked_part.select(pl.corr("x", "y")).item()
        if corr is not None:
            monthly.append(
                {"date": str(day), "corr": round(float(corr), 6), "names": part.height}
            )
    corr_values = [row["corr"] for row in monthly]
    checks = {
        "mean_reduction_vs_r257": mean_reduction is not None and mean_reduction >= 0.01,
        "share_reduced": share_reduced is not None and share_reduced >= 0.20,
        "penalty_cap_respected": max_reduction is not None
        and max_reduction <= PENALTY_CAP + 1e-12,
        "overlap_median_constructive": overlap_median is not None
        and 0.60 <= overlap_median <= 0.95,
        "share_days_below_full_overlap": share_days_below_one is not None
        and share_days_below_one >= 0.50,
    }
    return {
        "months": len(corr_values),
        "monthly_rank_correlation": monthly,
        "mean_reduction_vs_r257_rank": round(mean_reduction, 6)
        if mean_reduction is not None
        else None,
        "share_reduced": round(share_reduced, 6) if share_reduced is not None else None,
        "max_reduction": round(max_reduction, 6) if max_reduction is not None else None,
        "top30_overlap_median": round(overlap_median, 6)
        if overlap_median is not None
        else None,
        "top30_share_days_below_full_overlap": round(share_days_below_one, 6)
        if share_days_below_one is not None
        else None,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
    }


def _month_end_membership(
    membership: pl.DataFrame, days: list[date]
) -> dict[date, set[str]]:
    snapshot_dates = (
        membership.select("effective_date")
        .unique()
        .sort("effective_date")["effective_date"]
        .to_list()
    )
    mapping = {
        key[0]: set(part["symbol"].to_list())
        for key, part in membership.group_by("effective_date", maintain_order=True)
    }
    import bisect

    out: dict[date, set[str]] = {}
    for day in days:
        position = bisect.bisect_right(snapshot_dates, day) - 1
        out[day] = mapping[snapshot_dates[position]] if position >= 0 else set()
    return out


def _rank_ic(
    frame: pl.DataFrame, left: str, right: str, minimum: int = 30
) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if part.height < minimum or part[left].n_unique() < 2 or part[right].n_unique() < 2:
        return None
    ranked = part.select(
        pl.col(left).rank("average").alias("x"),
        pl.col(right).rank("average").alias("y"),
    )
    value = ranked.select(pl.corr("x", "y")).item()
    return None if value is None else float(value)


def _eligible_rebalance_days(
    month_ends: list[date], calendar: list[date], start: date, end: date
) -> list[date]:
    calendar_index = {day: index for index, day in enumerate(calendar)}
    return sorted(
        {
            day
            for day in month_ends
            if start <= day <= end
            and calendar_index.get(day) is not None
            and calendar_index[day] + 21 < len(calendar)
            and calendar[calendar_index[day] + 21] <= end
        }
    )


def _factor_ic(
    factor_all: pl.DataFrame, r166, membership: pl.DataFrame, calendar: list[date]
) -> dict:
    all_month_ends = [day for day in r166._month_ends() if START <= day <= END]
    forward = r166._forward_returns(calendar, all_month_ends)
    membership_by_day = _month_end_membership(membership, all_month_ends)
    panel = forward.join(
        factor_all.rename({"date": "rebalance_date"}),
        on=["rebalance_date", "symbol"],
        how="inner",
    )
    out = {}
    for name, (start, end) in {
        "discovery": SEGMENTS[0],
        "validation": SEGMENTS[1],
    }.items():
        allowed_days = _eligible_rebalance_days(all_month_ends, calendar, start, end)
        monthly = []
        for day in allowed_days:
            rows = panel.filter(pl.col("rebalance_date") == day)
            symbols = membership_by_day.get(day, set())
            part = rows.filter(pl.col("symbol").is_in(symbols))
            ic = _rank_ic(part, FACTOR_COLUMN, "forward_return")
            if ic is not None:
                monthly.append(
                    {"date": str(day), "ic": round(ic, 6), "names": part.height}
                )
        values = [row["ic"] for row in monthly]
        out[name] = {
            "months": len(values),
            "mean_ic": round(sum(values) / len(values), 6) if values else None,
            "positive_rate": round(sum(value > 0 for value in values) / len(values), 4)
            if values
            else None,
            "monthly": monthly,
        }
    return out


def _segmented_results(
    service, membership, r112, r229, sid: str, cost: float, bt_helper
):
    curves = []
    runs = []
    trades = []
    bt_helper.CACHE = CACHE
    for start, end in SEGMENTS:
        curve, info, segment_trades = bt_helper._run_segment(
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
    bt_helper = _load(R253_SCRIPT, "bt_helper_r260")
    builder = _load(R260_BUILDER, "r260_builder")
    base = _load(BASE_BUILDER, "base_builder_r260_backtest")

    calendar = r229._benchmark_dates(START, END)
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    universe = r221._universe_audit(all_members)
    service = r112._service()

    factor_all = _factor_rows()
    r257_all = _r257_rows()
    coverage = _coverage(membership, factor_all)
    pit_audit = _pit_audit(factor_all, builder, base)
    increment = _increment_audit(factor_all, r257_all, membership, r166)
    factor_ic = _factor_ic(factor_all, r166, membership, calendar)

    results = {}
    trades = {}
    for name, sid in STRATEGIES.items():
        result, run_trades = _segmented_results(
            service, membership, r112, r229, sid, 3.0, bt_helper
        )
        results[name] = result
        trades[name] = run_trades

    stress = {}
    for cost in (4.0, 5.0):
        result, _ = _segmented_results(
            service,
            membership,
            r112,
            r229,
            STRATEGIES["candidate"],
            cost,
            bt_helper,
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
    d1 = (
        coverage["full"]["coverage"] >= 0.99
        and coverage["recent_12m"]["coverage"] >= 0.99
    )
    d2 = (
        coverage["discovery"]["coverage"] >= 0.99
        and coverage["validation"]["coverage"] >= 0.99
    )
    d3 = coverage["first_valid_date"] <= str(START) and pit_audit["all_checks_pass"]
    d4 = increment["all_checks_pass"]
    i1 = (
        factor_ic["discovery"]["months"] >= 18
        and (factor_ic["discovery"]["mean_ic"] or 0.0) >= 0.02
    )
    i2 = (
        factor_ic["validation"]["months"] >= 18
        and (factor_ic["validation"]["mean_ic"] or 0.0) >= 0.01
        and (factor_ic["validation"]["mean_ic"] or 0.0) > 0
    )
    g1 = (
        candidate["discovery_2023_2024"]["total_return"]
        >= control["discovery_2023_2024"]["total_return"] + 0.05
    )
    g2 = (
        candidate["discovery_2023_2024"]["mdd"]
        >= control["discovery_2023_2024"]["mdd"] - 0.05
    )
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
    c2 = (
        concentration_full["total_pnl_amount"] > 0
        and concentration_full["top1_net_share"] <= 0.25
    )
    c3 = (
        concentration_full["total_pnl_amount"] > 0
        and concentration_full["top3_net_share"] <= 0.55
    )
    c4 = (
        concentration_full["total_pnl_amount"] > 0
        and concentration_full["top5_net_share"] <= 0.75
    )
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
        "round": ROUND,
        "purpose": (
            "R260 bounded soft-freshness penalty on R257 versus the matched R257 control"
        ),
        "preregistration": (EVIDENCE / "r260_preregistration.json")
        .relative_to(ROOT)
        .as_posix(),
        "data_feasibility": (EVIDENCE / "r260_data_feasibility.json")
        .relative_to(ROOT)
        .as_posix(),
        "window": [str(START), str(END)],
        "trading_days": len(calendar),
        "universe": universe,
        "factor": {
            "id": FACTOR_ID,
            "config_id": FACTOR_CONFIG,
            "column": FACTOR_COLUMN,
            "direction": "high",
            "formula": (
                "quality_rs_soft_fresh_rank = rank_pct(quality_relative_strength_min_rank) * "
                "(1 - 0.20 * prior20_r257_top30_count / 20) within historical nonfinancial "
                "HS300 members"
            ),
            "components": [
                {"id": R257_FACTOR_ID, "column": R257_COLUMN},
                {
                    "name": "prior20_r257_top30_count",
                    "lookback": PENALTY_LOOKBACK,
                    "penalty_cap": PENALTY_CAP,
                },
            ],
            "pit_rule": "Prior R257 Top30 counts use only the preceding 20 benchmark trading dates; current ranks use same-date eligible historical nonfinancial HS300 members",
            "coverage": coverage,
            "pit_audit": pit_audit,
            "increment_audit": increment,
            "factor_ic": factor_ic,
        },
        "samples": {
            "discovery": ["2023-05-04", "2024-12-31"],
            "validation": ["2025-01-02", str(END)],
            "rolling_1y": "252-trading-day rolling compounded return over the stitched 2023-2026 curve",
            "recent_12m": "last 252 trading-day returns of the stitched 2023-2026 curve",
        },
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
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
                key: value
                for key, value in concentration_full.items()
                if key != "per_symbol"
            },
            "validation": {
                key: value
                for key, value in concentration_validation.items()
                if key != "per_symbol"
            },
            "validation_positive_symbols": validation_positive,
            "per_symbol_full": concentration_full["per_symbol"],
        },
        "decision": decision,
        "caveats": [
            "The soft-freshness design is a result-driven follow-up to R257/R258/R259 and is not independent confirmation.",
            "The 20-day recency count depends on strict Top30 tie-breaking and complete benchmark dates.",
            "A 20% maximum haircut may be too gentle to change concentration.",
            "R253 and industry-relative strength were researched in overlapping samples, so multiple-comparison risk remains.",
            "The industry snapshot used to exclude financials is current rather than historical PIT.",
            "Financial values inherit provider restatement and publication-timing risk.",
            "The full curve is stitched from two system-engine segments; segment boundaries can reset an open position.",
            "Trade P&L shares are contribution diagnostics from two segments each starting at 1,000,000 notional.",
            "Passing every gate would justify paper-trading validation, not production deployment.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "coverage": coverage,
                "pit_audit": pit_audit,
                "increment": increment,
                "factor_ic": factor_ic,
                "results": results,
                "stress": {
                    key: value["result"]["full"] for key, value in stress.items()
                },
                "concentration": payload["concentration"]["full"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
