#!/usr/bin/env python3
"""R260 feasibility audit for a soft 20-day recency penalty on R257."""

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
OUT = EVIDENCE / "r260_data_feasibility.json"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R253_BUILDER = ROOT / "scripts" / "build_r253_min_rank_factor.py"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
RECENT_WINDOW = 252
R257_CONFIG = "alt_hs300_qrs"
R257_FIELD = "quality_relative_strength_min_rank"
FACTOR_FIELD = "quality_rs_soft_fresh_rank"
LOOKBACK = 20
TOP_N = 30
PENALTY_CAP = 0.20


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _factor_frame() -> pl.DataFrame:
    root = DATA / "ext_data" / R257_CONFIG / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"R257 factor is missing: {root}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", R257_FIELD])
        .collect()
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col(R257_FIELD).cast(pl.Float64),
        )
        .sort(["date", "symbol"])
    )


def _strict_top30(frame: pl.DataFrame, field: str) -> pl.DataFrame:
    return (
        frame.sort(["date", field, "symbol"], descending=[False, True, False])
        .with_columns(
            pl.int_range(1, pl.len() + 1)
            .over("date")
            .cast(pl.Int64)
            .alias("_strict_rank")
        )
        .filter(pl.col("_strict_rank") <= TOP_N)
        .select(["date", "symbol"])
    )


def _sample_stats(frame: pl.DataFrame, start: date, end: date) -> dict:
    sample = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    eligible = sample.filter(pl.col("_member") == 1)
    valid = eligible.filter(pl.col(R257_FIELD).is_not_null())
    daily = valid.group_by("date").len().sort("date")
    return {
        "eligible_member_rows": eligible.height,
        "valid_member_rows": valid.height,
        "coverage": round(valid.height / eligible.height, 6)
        if eligible.height
        else 0.0,
        "eligible_dates": eligible["date"].n_unique(),
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
    r257 = _factor_frame()
    universe = member_daily.select("symbol").unique().sort("symbol")
    history = (
        pl.DataFrame({"date": calendar}, schema={"date": pl.Date})
        .join(universe, how="cross")
        .join(
            member_daily.with_columns(pl.lit(1).cast(pl.Int8).alias("_member")),
            on=["date", "symbol"],
            how="left",
        )
        .join(r257, on=["date", "symbol"], how="left")
        .with_columns(pl.col("_member").fill_null(0).cast(pl.Int8))
    )
    current_top30 = _strict_top30(
        history.filter(pl.col("_member") == 1), R257_FIELD
    ).with_columns(pl.lit(1).cast(pl.Int8).alias("_r257_top30"))
    history = (
        history.join(current_top30, on=["date", "symbol"], how="left")
        .with_columns(pl.col("_r257_top30").fill_null(0).cast(pl.Int8))
        .sort(["symbol", "date"])
        .with_columns(
            pl.col("_r257_top30")
            .rolling_sum(window_size=LOOKBACK, min_samples=1)
            .shift(1)
            .fill_null(0)
            .cast(pl.Int8)
            .over("symbol")
            .alias("_prior_top30_count")
        )
    )
    valid = history.filter((pl.col("_member") == 1) & pl.col(R257_FIELD).is_not_null())
    scored = (
        valid.with_columns(
            pl.col(R257_FIELD)
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
        .with_columns(
            (
                pl.col("_r257_pct")
                * (
                    1.0
                    - PENALTY_CAP
                    * pl.col("_prior_top30_count").cast(pl.Float64)
                    / LOOKBACK
                )
            ).alias(FACTOR_FIELD)
        )
        .sort(["date", "symbol"])
    )
    soft_top30 = _strict_top30(scored, FACTOR_FIELD).with_columns(
        pl.lit(1).cast(pl.Int8).alias("_soft_top30")
    )
    overlap = (
        scored.select(["date", "symbol", "_prior_top30_count"])
        .join(soft_top30, on=["date", "symbol"], how="inner")
        .join(
            current_top30.select(["date", "symbol", "_r257_top30"]),
            on=["date", "symbol"],
            how="inner",
        )
        .group_by("date")
        .agg(pl.col("_r257_top30").sum().alias("_overlap"))
        .sort("date")
    )
    overlap = overlap.with_columns(
        (pl.col("_overlap").cast(pl.Float64) / TOP_N).alias("_overlap")
    )
    overlap_values = overlap["_overlap"]

    daily_counts = scored.group_by("date").len().sort("date")
    top_recency = scored.join(
        soft_top30.select(["date", "symbol"]), on=["date", "symbol"], how="inner"
    )["_prior_top30_count"]
    reduction = scored.select(
        (pl.col("_r257_pct") - pl.col(FACTOR_FIELD)).alias("_reduction")
    )["_reduction"]
    factor_stats = {
        "rows": scored.height,
        "dates": scored["date"].n_unique(),
        "symbols": scored["symbol"].n_unique(),
        "min": round(float(scored[FACTOR_FIELD].min()), 6),
        "p01": round(float(scored[FACTOR_FIELD].quantile(0.01)), 6),
        "median": round(float(scored[FACTOR_FIELD].median()), 6),
        "p99": round(float(scored[FACTOR_FIELD].quantile(0.99)), 6),
        "max": round(float(scored[FACTOR_FIELD].max()), 6),
        "mean_reduction_vs_r257_rank": round(float(reduction.mean()), 6),
        "share_reduced": round(float((reduction > 0).mean()), 6),
        "share_reduced_by_at_least_005": round(float((reduction >= 0.05).mean()), 6),
        "max_reduction": round(float(reduction.max()), 6),
    }
    overlap_stats = {
        "days": overlap.height,
        "min": round(float(overlap_values.min()), 6),
        "p05": round(float(overlap_values.quantile(0.05)), 6),
        "median": round(float(overlap_values.median()), 6),
        "p95": round(float(overlap_values.quantile(0.95)), 6),
        "max": round(float(overlap_values.max()), 6),
        "share_days_below_one": round(float((overlap_values < 1.0).mean()), 6),
    }
    recency_stats = {
        "top30_count_min": int(top_recency.min()),
        "top30_count_p25": float(top_recency.quantile(0.25)),
        "top30_count_median": float(top_recency.median()),
        "top30_count_p75": float(top_recency.quantile(0.75)),
        "top30_count_max": int(top_recency.max()),
        "top30_share_zero_count": round(float((top_recency == 0).mean()), 6),
        "top30_share_count_ge_10": round(float((top_recency >= 10).mean()), 6),
    }
    recent_start = calendar[-RECENT_WINDOW]
    samples = {
        "full": _sample_stats(history, START, END),
        "discovery": _sample_stats(history, START, date(2024, 12, 31)),
        "validation": _sample_stats(history, date(2025, 1, 2), END),
        "recent_12m": _sample_stats(history, recent_start, END),
    }
    formula_diff = scored.select(
        (
            pl.col("_r257_pct")
            * (
                1.0
                - PENALTY_CAP * pl.col("_prior_top30_count").cast(pl.Float64) / LOOKBACK
            )
            - pl.col(FACTOR_FIELD)
        )
        .abs()
        .max()
    ).item()
    checks = {
        "D1_full_coverage": samples["full"]["coverage"] >= 0.99,
        "D2_recent_coverage": samples["recent_12m"]["coverage"] >= 0.99,
        "D3_segment_coverage": (
            samples["discovery"]["coverage"] >= 0.99
            and samples["validation"]["coverage"] >= 0.99
        ),
        "D4_first_valid_by_start": samples["full"]["first_valid_date"] is not None
        and samples["full"]["first_valid_date"] <= str(START),
        "D5_soft_overlap_constructive": (
            overlap_stats["median"] >= 0.60
            and overlap_stats["median"] <= 0.95
            and overlap_stats["share_days_below_one"] >= 0.50
        ),
        "D6_penalty_incremental": (
            factor_stats["mean_reduction_vs_r257_rank"] >= 0.01
            and factor_stats["share_reduced"] >= 0.20
            and factor_stats["max_reduction"] <= PENALTY_CAP + 1e-12
        ),
        "D7_daily_capacity": (
            samples["full"]["min_daily_names"] >= TOP_N
            and daily_counts.height == len(calendar)
        ),
        "D8_formula": formula_diff is not None and float(formula_diff) <= 1e-12,
    }
    payload = {
        "round": "R260",
        "status": "feasibility_only_no_forward_returns",
        "pre_return_audit_note": (
            "The first feasibility run failed D7 because the probe incorrectly required a "
            "constant daily member count. Before reading any return, D7 was corrected to "
            "require at least 30 valid names on every benchmark date. The formula, universe, "
            "penalty cap, lookback and other thresholds were unchanged."
        ),
        "hypothesis": (
            "R257's P&L concentration comes from repeatedly selected winners, but R258's hard "
            "freshness gate eliminated the candidate pool and R259's reversal min-rank destroyed "
            "returns. A bounded soft recency penalty can demote persistent top-30 names without "
            "excluding any stock."
        ),
        "selection_basis": (
            "This is a result-driven follow-up informed by R257's C2/C3/C4 failure, R258's "
            "daily fresh-name median of one and R259's G1-G9 failure. It does not claim "
            "independent confirmation. The penalty cap is fixed at 20% before reading any "
            "R260 forward or strategy return."
        ),
        "factor": {
            "provisional_id": "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank",
            "config_id": "alt_hs300_qrsrf",
            "column": FACTOR_FIELD,
            "formula": (
                "rank_pct(quality_relative_strength_min_rank) * "
                "(1 - 0.20 * prior20_r257_top30_count / 20) within historical nonfinancial "
                "HS300 members"
            ),
            "direction": "high",
            "lookback": LOOKBACK,
            "top_n": TOP_N,
            "penalty_cap": PENALTY_CAP,
            "pit_rule": (
                "Prior top-30 membership uses only the preceding 20 benchmark trading dates. "
                "Strict top-30 uses factor descending then symbol ascending. Current-date ranks "
                "use only current eligible historical nonfinancial HS300 members."
            ),
        },
        "window": [str(START), str(END)],
        "trading_days": len(calendar),
        "recent_window_start": str(recent_start),
        "samples": samples,
        "factor_stats": factor_stats,
        "top30_overlap_with_r257": overlap_stats,
        "soft_top30_recency": recency_stats,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "selection_note": (
            "These thresholds are fixed before reading any R260 forward return. If all checks "
            "pass, the factor and candidate/control strategies will be pre-registered before "
            "implementation."
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
