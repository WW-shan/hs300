#!/usr/bin/env python3
"""R258 feasibility audit for a 20-day fresh-entry gate on R257, without returns."""

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
OUT = EVIDENCE / "r258_data_feasibility.json"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R253_BUILDER = ROOT / "scripts" / "build_r253_min_rank_factor.py"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
R257_CONFIG = "alt_hs300_qrs"
R257_FIELD = "quality_relative_strength_min_rank"
TOP_N = 30
COOLDOWN = 20


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
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
        .sort(["date", "symbol"])
    )


def _sample_stats(frame: pl.DataFrame, start: date, end: date) -> dict:
    sample = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    return {
        "dates": sample["date"].n_unique(),
        "top_rows": int((sample["_top30"] == 1).sum()),
        "fresh_rows": int((sample["_fresh"] == 1).sum()),
        "fresh_share_of_top30": round(
            float((sample["_fresh"] == 1).sum())
            / max(int((sample["_top30"] == 1).sum()), 1),
            6,
        ),
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
    factor = _factor_frame()
    eligible = (
        member_daily.join(factor, on=["date", "symbol"], how="left")
        .sort(["date", "symbol"])
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_member_count"))
    )
    valid = eligible.filter(pl.col(R257_FIELD).is_not_null())
    strict_top = (
        valid.sort(["date", R257_FIELD, "symbol"], descending=[False, True, False])
        .with_columns(
            pl.int_range(1, pl.len() + 1)
            .over("date")
            .cast(pl.Int64)
            .alias("_strict_rank")
        )
        .filter(pl.col("_strict_rank") <= TOP_N)
        .select(["date", "symbol", "_strict_rank"])
    )
    universe = member_daily.select("symbol").unique().sort("symbol")
    ranked = (
        pl.DataFrame({"date": calendar}, schema={"date": pl.Date})
        .join(universe, how="cross")
        .join(
            member_daily.with_columns(pl.lit(1).cast(pl.Int8).alias("_member")),
            on=["date", "symbol"],
            how="left",
        )
        .join(factor, on=["date", "symbol"], how="left")
        .join(strict_top, on=["date", "symbol"], how="left")
        .with_columns(
            pl.col("_member").fill_null(0).cast(pl.Int8),
            pl.col("_strict_rank").is_not_null().cast(pl.Int8).alias("_top30"),
        )
        .sort(["symbol", "date"])
        .with_columns(
            pl.col("_top30")
            .rolling_max(window_size=COOLDOWN, min_samples=1)
            .shift(1)
            .fill_null(0)
            .cast(pl.Int8)
            .over("symbol")
            .alias("_prior_top30")
        )
        .with_columns(
            ((pl.col("_top30") == 1) & (pl.col("_prior_top30") == 0))
            .cast(pl.Int8)
            .alias("_fresh")
        )
        .sort(["date", "symbol"])
    )
    daily = (
        ranked.group_by("date")
        .agg(
            pl.col("_top30").sum().alias("top30_names"),
            pl.col("_fresh").sum().alias("fresh_names"),
        )
        .sort("date")
    )
    counts = daily["fresh_names"]
    period_counts = {
        "days": daily.height,
        "min_top30_names": int(daily["top30_names"].min()),
        "max_top30_names": int(daily["top30_names"].max()),
        "min": int(counts.min()),
        "p05": int(counts.quantile(0.05)),
        "median": float(counts.median()),
        "p95": int(counts.quantile(0.95)),
        "max": int(counts.max()),
        "days_ge_10": int((counts >= 10).sum()),
        "days_ge_20": int((counts >= 20).sum()),
        "days_ge_30": int((counts >= 30).sum()),
        "share_days_ge_10": round(float((counts >= 10).mean()), 6),
        "share_days_ge_20": round(float((counts >= 20).mean()), 6),
        "share_days_ge_30": round(float((counts >= 30).mean()), 6),
    }
    samples = {
        "full": _sample_stats(ranked, START, END),
        "discovery": _sample_stats(ranked, START, date(2024, 12, 31)),
        "validation": _sample_stats(ranked, date(2025, 1, 2), END),
        "recent_12m": _sample_stats(ranked, calendar[-252], END),
    }
    coverage = round(valid.height / eligible.height, 6) if eligible.height else 0.0
    checks = {
        "D1_factor_coverage": coverage >= 0.99,
        "D2_strict_top30_count": int(daily["top30_names"].min()) == TOP_N
        and int(daily["top30_names"].max()) == TOP_N,
        "D3_min_fresh_names": int(counts.min()) >= 10,
        "D4_median_fresh_names": float(counts.median()) >= 20,
        "D5_available_on_95pct_days": float((counts >= 20).mean()) >= 0.95,
        "D6_fresh_share_constructive": 0.20
        <= samples["full"]["fresh_share_of_top30"]
        <= 0.80,
        "D7_full_dates": daily.height == len(calendar),
    }
    payload = {
        "round": "R258",
        "status": "feasibility_only_no_forward_returns",
        "hypothesis": (
            "R257's P&L is too concentrated in repeated winners. A 20-trading-day fresh-entry gate, which "
            "prevents a stock from re-entering the R257 top-30 score pool after it appeared there in the prior "
            "20 trading days, should diversify realized P&L without changing the R257 factor or 10-position limits."
        ),
        "selection_basis": (
            "R257 passed all D/I/G gates and all cost-stress tests but failed C2/C3/C4 with top1/top3/top5 net "
            "P&L shares of 27.95%/64.08%/94.43%. This is a follow-up, result-driven concentration hypothesis, "
            "not independent confirmation. No R258 forward or strategy returns are read in this audit."
        ),
        "gate_factor": {
            "provisional_id": "ext_alt_hs300_qrs_fresh20_flag",
            "config_id": "alt_hs300_qrs_fresh20",
            "column": "fresh_quality_rs_flag",
            "formula": (
                "1 if R257 factor is in the top 30 eligible historical nonfinancial HS300 members on date t "
                "and the stock was not in the top 30 on any of the prior 20 trading dates in the common benchmark calendar; otherwise 0"
            ),
            "direction": "gate",
            "lookback": COOLDOWN,
            "top_n": TOP_N,
            "pit_rule": "top-30 membership and the prior-window maximum use only information available on or before date t",
        },
        "window": [str(START), str(END)],
        "coverage": coverage,
        "eligible_member_rows": eligible.height,
        "history_grid_rows": ranked.height,
        "history_symbols": universe.height,
        "valid_r257_rows": valid.height,
        "daily_fresh_name_counts": period_counts,
        "samples": samples,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "selection_note": (
            "These feasibility constraints are fixed before reading any strategy return. If they pass, the "
            "fresh-entry factor and candidate/control strategies will be pre-registered before implementation."
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
