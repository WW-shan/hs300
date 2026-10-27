#!/usr/bin/env python3
"""R261 data-feasibility audit for quality-relative strength with low lottery demand."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

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
OUT = EVIDENCE / "r261_data_feasibility.json"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R253_BUILDER = ROOT / "scripts" / "build_r253_min_rank_factor.py"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
RECENT_WINDOW = 252
R257_CONFIG = "alt_hs300_qrs"
R257_FIELD = "quality_relative_strength_min_rank"
R249_CONFIG = "alt_hs300_skew"
R249_FIELD = "stock_skew_60d_low"
LOTTERY_WINDOW = 20
LOW_LOTTERY_FIELD = "low_max_ret_20d"
COMPOSITE_FIELD = "quality_low_lottery_min_rank"
MIN_NAMES = 30


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _factor_frame(config_id: str, field: str) -> pl.DataFrame:
    root = DATA / "ext_data" / config_id / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"extension factor is missing: {root}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", field])
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col(field).cast(pl.Float64),
        )
        .sort(["date", "symbol"])
    )


def _low_lottery_frame() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("enriched daily kline is missing")
    prices = (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", "close"])
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col("close").cast(pl.Float64),
        )
        .unique(subset=["symbol", "date"], keep="last")
        .sort(["symbol", "date"])
    )
    previous = pl.col("close").shift(1).over("symbol")
    daily_return = (
        pl.when((pl.col("close") > 0) & (previous > 0))
        .then(pl.col("close") / previous - 1)
        .otherwise(None)
    )
    return (
        prices.with_columns(daily_return.alias("_daily_return"))
        .with_columns(
            pl.col("_daily_return")
            .rolling_max(window_size=LOTTERY_WINDOW, min_samples=LOTTERY_WINDOW)
            .over("symbol")
            .alias("_max_ret_20d")
        )
        .with_columns((-pl.col("_max_ret_20d")).alias(LOW_LOTTERY_FIELD))
        .select(["symbol", "date", LOW_LOTTERY_FIELD])
    )


def _month_ends(calendar: list[date]) -> list[date]:
    return (
        pl.DataFrame({"date": calendar}, schema={"date": pl.Date})
        .with_columns(
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
        )
        .group_by(["year", "month"])
        .agg(pl.col("date").max())
        .sort("date")["date"]
        .to_list()
    )


def _rank_corr(frame: pl.DataFrame, left: str, right: str) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if (
        part.height < MIN_NAMES
        or part[left].n_unique() < 2
        or part[right].n_unique() < 2
    ):
        return None
    ranked = part.select(
        pl.col(left).rank("average").alias("x"),
        pl.col(right).rank("average").alias("y"),
    )
    value = ranked.select(pl.corr("x", "y")).item()
    return None if value is None else float(value)


def _sample_stats(frame: pl.DataFrame, start: date, end: date) -> dict:
    sample = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    valid = sample.filter(
        pl.all_horizontal(
            [pl.col(R257_FIELD).is_not_null(), pl.col(LOW_LOTTERY_FIELD).is_not_null()]
        )
    )
    daily = valid.group_by("date").len().sort("date")
    return {
        "eligible_member_rows": sample.height,
        "valid_member_rows": valid.height,
        "coverage": round(valid.height / sample.height, 6) if sample.height else 0.0,
        "eligible_dates": sample["date"].n_unique(),
        "valid_dates": valid["date"].n_unique(),
        "first_valid_date": str(valid["date"].min()) if valid.height else None,
        "last_valid_date": str(valid["date"].max()) if valid.height else None,
        "valid_symbols": valid["symbol"].n_unique() if valid.height else 0,
        "min_daily_names": int(daily["len"].min()) if daily.height else 0,
        "median_daily_names": float(daily["len"].median()) if daily.height else 0.0,
    }


def main() -> int:
    r229 = _load(R229_SCRIPT, "r229")
    r253_builder = _load(R253_BUILDER, "r253_builder")
    base = _load(BASE_BUILDER, "base_builder")
    calendar = r229._benchmark_dates(START, END)
    membership = r253_builder._membership_with_industry(base)
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")
    member_daily = (
        pl.DataFrame({"date": calendar}, schema={"date": pl.Date})
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
    r257 = _factor_frame(R257_CONFIG, R257_FIELD)
    low_lottery = _low_lottery_frame()
    eligible = (
        member_daily.join(r257, on=["date", "symbol"], how="left")
        .join(low_lottery, on=["date", "symbol"], how="left")
        .sort(["date", "symbol"])
    )
    valid = eligible.filter(
        pl.all_horizontal(
            [pl.col(R257_FIELD).is_not_null(), pl.col(LOW_LOTTERY_FIELD).is_not_null()]
        )
    )

    recent_start = calendar[-RECENT_WINDOW]
    samples = {
        "full": _sample_stats(eligible, START, END),
        "discovery": _sample_stats(eligible, START, date(2024, 12, 31)),
        "validation": _sample_stats(eligible, date(2025, 1, 2), END),
        "recent_12m": _sample_stats(eligible, recent_start, END),
    }

    monthly_correlations = []
    for day in _month_ends(calendar):
        value = _rank_corr(
            valid.filter(pl.col("date") == day), R257_FIELD, LOW_LOTTERY_FIELD
        )
        if value is not None:
            monthly_correlations.append({"date": str(day), "corr": round(value, 6)})
    corr_values = [float(row["corr"]) for row in monthly_correlations]
    skew = _factor_frame(R249_CONFIG, R249_FIELD)
    valid_with_skew = valid.join(skew, on=["date", "symbol"], how="left")
    skew_correlations = []
    for day in _month_ends(calendar):
        value = _rank_corr(
            valid_with_skew.filter(pl.col("date") == day),
            LOW_LOTTERY_FIELD,
            R249_FIELD,
        )
        if value is not None:
            skew_correlations.append({"date": str(day), "corr": round(value, 6)})
    skew_corr_values = [float(row["corr"]) for row in skew_correlations]

    ranked = (
        valid.with_columns(
            [
                pl.col(column)
                .rank("average", descending=True)
                .over("date")
                .cast(pl.Float64)
                .alias(f"_rank_{column}")
                for column in (R257_FIELD, LOW_LOTTERY_FIELD)
            ]
        )
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_names"))
        .filter(pl.col("_names") > 1)
        .with_columns(
            [
                (
                    (pl.col("_names") - pl.col(f"_rank_{column}"))
                    / (pl.col("_names") - 1.0)
                ).alias(f"_pct_{column}")
                for column in (R257_FIELD, LOW_LOTTERY_FIELD)
            ]
        )
        .with_columns(
            pl.min_horizontal(
                [
                    pl.col(f"_pct_{R257_FIELD}"),
                    pl.col(f"_pct_{LOW_LOTTERY_FIELD}"),
                ]
            ).alias(COMPOSITE_FIELD)
        )
    )
    difference = ranked.select(
        (pl.col(f"_pct_{R257_FIELD}") - pl.col(COMPOSITE_FIELD)).alias("_diff")
    )["_diff"]
    composite_stats = {
        "rows": ranked.height,
        "dates": ranked["date"].n_unique(),
        "symbols": ranked["symbol"].n_unique(),
        "min": round(float(ranked[COMPOSITE_FIELD].min()), 6),
        "p01": round(float(ranked[COMPOSITE_FIELD].quantile(0.01)), 6),
        "median": round(float(ranked[COMPOSITE_FIELD].median()), 6),
        "p99": round(float(ranked[COMPOSITE_FIELD].quantile(0.99)), 6),
        "max": round(float(ranked[COMPOSITE_FIELD].max()), 6),
        "zero_rate": round(float((ranked[COMPOSITE_FIELD] == 0.0).mean()), 6),
        "mean_difference_from_r257_rank": round(float(difference.mean()), 6),
        "median_difference_from_r257_rank": round(float(difference.median()), 6),
        "share_reduced_by_at_least_010": round(float((difference >= 0.10).mean()), 6),
    }

    checks = {
        "D1_full_coverage": samples["full"]["coverage"] >= 0.99,
        "D2_recent_coverage": samples["recent_12m"]["coverage"] >= 0.99,
        "D3_segment_coverage": (
            samples["discovery"]["coverage"] >= 0.99
            and samples["validation"]["coverage"] >= 0.99
        ),
        "D4_first_valid_by_start": samples["full"]["first_valid_date"] is not None
        and samples["full"]["first_valid_date"] <= str(START),
        "D5_component_correlation": bool(corr_values)
        and sum(abs(value) for value in corr_values) / len(corr_values) <= 0.70
        and max(abs(value) for value in corr_values) <= 0.80,
        "D6_incremental_information": (
            composite_stats["mean_difference_from_r257_rank"] >= 0.05
            and composite_stats["share_reduced_by_at_least_010"] >= 0.20
        ),
        "D7_daily_capacity": (
            samples["full"]["min_daily_names"] >= MIN_NAMES
            and samples["full"]["valid_dates"] == len(calendar)
            and len(corr_values) >= 40
        ),
        "D8_low_skew_independence": bool(skew_corr_values)
        and sum(abs(value) for value in skew_corr_values) / len(skew_corr_values)
        <= 0.70
        and max(abs(value) for value in skew_corr_values) <= 0.80,
    }
    payload = {
        "round": "R261",
        "status": "feasibility_only_no_forward_returns",
        "hypothesis": (
            "R257's realized P&L was dominated by repeated strength winners. A low 20-day "
            "maximum-single-day-return weakest link should retain quality and industry-relative "
            "leadership while avoiding lottery-like one-day spikes. This is distinct from a broad "
            "5-day reversal and does not change R257's internal formula or portfolio constraints."
        ),
        "selection_basis": (
            "R257 passed all predictive, return and cost-stress gates but failed concentration "
            "C2/C3/C4 because top1/top3/top5 net P&L shares were 27.95%/64.08%/94.43%. R258's "
            "20-day re-entry gate failed before returns because it left a median of only one fresh "
            "name, and R260's soft recency penalty reduced industry concentration but broke "
            "validation and recent returns. R261 tests a different mechanism: penalizing one-day "
            "lottery-like jumps rather than total short-term momentum. No R261 forward or strategy "
            "returns are read in this audit."
        ),
        "factor": {
            "provisional_id": "ext_alt_hs300_qlot_quality_low_lottery_min_rank",
            "config_id": "alt_hs300_qlot",
            "column": COMPOSITE_FIELD,
            "formula": (
                "min(rank_pct(quality_relative_strength_min_rank), "
                "rank_pct(-max_ret_20d)) within historical nonfinancial HS300 members"
            ),
            "direction": "high",
            "components": [R257_FIELD, LOW_LOTTERY_FIELD],
            "pit_rule": (
                "R257 inherits financial publication/PIT dates and same-date industry-relative "
                "strength; max_ret_20d is the maximum daily front-adjusted close-to-close return "
                "over the current and prior 19 trading days. Ranks and the minimum use only "
                "same-date eligible historical nonfinancial HS300 members."
            ),
        },
        "window": [str(START), str(END)],
        "trading_days": len(calendar),
        "recent_window_start": str(recent_start),
        "samples": samples,
        "eligible_member_rows": eligible.height,
        "joint_valid_rows": valid.height,
        "r249_low_skew_monthly_rank_correlation": {
            "months": len(skew_corr_values),
            "mean_abs": round(
                sum(abs(value) for value in skew_corr_values) / len(skew_corr_values), 6
            )
            if skew_corr_values
            else None,
            "max_abs": round(max(abs(value) for value in skew_corr_values), 6)
            if skew_corr_values
            else None,
            "monthly": skew_correlations,
        },
        "component_monthly_rank_correlation": {
            "months": len(corr_values),
            "mean": round(sum(corr_values) / len(corr_values), 6)
            if corr_values
            else None,
            "mean_abs": round(
                sum(abs(value) for value in corr_values) / len(corr_values), 6
            )
            if corr_values
            else None,
            "min": round(min(corr_values), 6) if corr_values else None,
            "max": round(max(corr_values), 6) if corr_values else None,
            "monthly": monthly_correlations,
        },
        "composite_preview": composite_stats,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "selection_note": (
            "These feasibility constraints are fixed before reading any R261 forward return. "
            "If all checks pass, the factor and candidate/control strategies will be "
            "pre-registered before implementation."
        ),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
