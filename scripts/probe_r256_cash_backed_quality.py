#!/usr/bin/env python3
"""R256 data-feasibility audit for cash-backed quality without reading returns."""

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
OUT = EVIDENCE / "r256_data_feasibility.json"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R253_BUILDER = ROOT / "scripts" / "build_r253_min_rank_factor.py"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
RECENT_WINDOW = 252
R253_CONFIG = "alt_hs300_minrank"
R253_FIELD = "management_ocf_gm_min_rank"
FCF_CONFIG = "alt_hs300_fcf"
FCF_FIELD = "free_cash_flow_assets"
COMPONENTS = (R253_FIELD, FCF_FIELD)
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
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
        .sort(["date", "symbol"])
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
        pl.all_horizontal([pl.col(field).is_not_null() for field in COMPONENTS])
    )
    return {
        "eligible_member_rows": sample.height,
        "valid_member_rows": valid.height,
        "coverage": round(valid.height / sample.height, 6) if sample.height else 0.0,
        "eligible_dates": sample["date"].n_unique(),
        "valid_dates": valid["date"].n_unique(),
        "first_valid_date": str(valid["date"].min()) if valid.height else None,
        "last_valid_date": str(valid["date"].max()) if valid.height else None,
        "valid_symbols": valid["symbol"].n_unique() if valid.height else 0,
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
    r253 = _factor_frame(R253_CONFIG, R253_FIELD)
    fcf = _factor_frame(FCF_CONFIG, FCF_FIELD)
    eligible = (
        member_daily.join(r253, on=["date", "symbol"], how="left")
        .join(fcf, on=["date", "symbol"], how="left")
        .sort(["date", "symbol"])
    )
    valid = eligible.filter(
        pl.all_horizontal([pl.col(field).is_not_null() for field in COMPONENTS])
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
        part = valid.filter(pl.col("date") == day)
        corr = _rank_corr(part, R253_FIELD, FCF_FIELD)
        if corr is not None:
            monthly_correlations.append(
                {"date": str(day), "corr": round(corr, 6), "names": part.height}
            )
    corr_values = [row["corr"] for row in monthly_correlations]

    ranked = (
        valid.with_columns(
            [
                pl.col(field)
                .rank("average", descending=True)
                .over("date")
                .cast(pl.Float64)
                .alias(f"_rank_{field}")
                for field in COMPONENTS
            ]
        )
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_names"))
        .filter(pl.col("_names") > 1)
        .with_columns(
            [
                (
                    (pl.col("_names") - pl.col(f"_rank_{field}"))
                    / (pl.col("_names") - 1.0)
                ).alias(f"_pct_{field}")
                for field in COMPONENTS
            ]
        )
    )
    composite_column = "cash_backed_quality_min_rank"
    composite = ranked.with_columns(
        pl.min_horizontal(
            [
                pl.col(f"_pct_{R253_FIELD}"),
                pl.col(f"_pct_{FCF_FIELD}"),
            ]
        ).alias(composite_column)
    )
    difference = composite.select(
        (pl.col(f"_pct_{R253_FIELD}") - pl.col(composite_column)).alias("_diff")
    )["_diff"]
    composite_stats = {
        "rows": composite.height,
        "dates": composite["date"].n_unique(),
        "symbols": composite["symbol"].n_unique(),
        "min": round(float(composite[composite_column].min()), 6),
        "p01": round(float(composite[composite_column].quantile(0.01)), 6),
        "median": round(float(composite[composite_column].median()), 6),
        "p99": round(float(composite[composite_column].quantile(0.99)), 6),
        "max": round(float(composite[composite_column].max()), 6),
        "zero_rate": round(float((composite[composite_column] == 0.0).mean()), 6),
        "mean_abs_difference_from_r253_rank": round(float(difference.abs().mean()), 6),
        "median_difference_from_r253_rank": round(float(difference.median()), 6),
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
        and sum(abs(value) for value in corr_values) / len(corr_values) <= 0.70,
        "D6_incremental_information": (
            composite_stats["mean_abs_difference_from_r253_rank"] >= 0.05
            and composite_stats["share_reduced_by_at_least_010"] >= 0.20
        ),
        "D7_monthly_observations": len(corr_values) >= 40,
    }
    payload = {
        "round": "R256",
        "status": "feasibility_only_no_forward_returns",
        "hypothesis": (
            "A quality company that also clears a free-cash-flow/assets rank should have more durable "
            "earnings quality than the R253 weakest-link alone; the new factor is the minimum of the "
            "R253 percentile and the FCF/assets percentile."
        ),
        "selection_basis": (
            "R253 passed both IC segments and most return gates but failed the recent year, cost stress "
            "and concentration. R246 FCF/assets had complete coverage plus positive validation and "
            "recent-year relative behavior. This audit checks whether the two signals have enough "
            "independent information to justify a new pre-registered factor; no forward or strategy "
            "returns are read."
        ),
        "factor": {
            "provisional_id": "ext_alt_hs300_qfcf_cash_backed_quality_min_rank",
            "config_id": "alt_hs300_qfcf",
            "column": composite_column,
            "formula": (
                "min(rank_pct(management_ocf_gm_min_rank), "
                "rank_pct(free_cash_flow_assets)) within historical nonfinancial HS300 members"
            ),
            "direction": "high",
            "components": list(COMPONENTS),
            "membership": "historical HS300 membership with current nonfinancial industry snapshot",
            "pit_rule": (
                "component values inherit their existing publication/PIT dates; percentile ranks and "
                "the minimum use same-date eligible membership only"
            ),
            "retired_outcome": "null",
        },
        "window": [str(START), str(END)],
        "trading_days": len(calendar),
        "recent_window_start": str(recent_start),
        "samples": samples,
        "eligible_member_rows": eligible.height,
        "joint_valid_rows": valid.height,
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
            "These thresholds are feasibility constraints fixed before reading any R256 forward return. "
            "If all checks pass, implementation and the return gates will be pre-registered before the "
            "factor table or strategies are created."
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
