# -*- coding: utf-8 -*-
"""R197 probe: HS300 new-high/new-low breadth availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r197_breadth_probe.json"
CACHE = DATA / "research" / "r197_breadth"
DAILY = CACHE / "daily.parquet"


def _prices() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(p) for p in paths])
        .select(["symbol", "date", "close"])
        .filter(pl.col("close") > 0)
        .collect()
        .sort(["symbol", "date"])
    )


def _membership() -> pl.DataFrame:
    monthly = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
    daily = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet")
    return (
        pl.concat([monthly, daily], how="diagonal_relaxed")
        .select([
            pl.col("query_date").cast(pl.Date).alias("snapshot_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["snapshot_date", "symbol"], keep="last")
        .sort(["snapshot_date", "symbol"])
    )


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    prices = _prices()
    prices = prices.with_columns([
        pl.col("close").rolling_max(window_size=20, min_samples=20).over("symbol").alias("_hi20"),
        pl.col("close").rolling_min(window_size=20, min_samples=20).over("symbol").alias("_lo20"),
    ]).with_columns([
        (pl.col("close") >= pl.col("_hi20")).alias("is_high"),
        (pl.col("close") <= pl.col("_lo20")).alias("is_low"),
    ]).filter(pl.col("_hi20").is_not_null())

    membership = _membership()
    trading_dates = pl.DataFrame({"date": prices["date"].unique().sort()})
    date_map = trading_dates.join_asof(
        membership.select(pl.col("snapshot_date")).unique().sort("snapshot_date"),
        left_on="date", right_on="snapshot_date", strategy="backward",
    )
    mapped = prices.join(date_map, on="date", how="left").join(
        membership, on=["snapshot_date", "symbol"], how="inner"
    )
    daily = (
        mapped.group_by("date")
        .agg([
            pl.len().alias("members"),
            pl.col("is_high").sum().alias("new_highs"),
            pl.col("is_low").sum().alias("new_lows"),
        ])
        .sort("date")
        .with_columns([
            (pl.col("new_highs") / pl.col("members")).alias("nh20_ratio"),
            (pl.col("new_lows") / pl.col("members")).alias("nl20_ratio"),
        ])
        .with_columns((pl.col("nh20_ratio") - pl.col("nl20_ratio")).alias("nh_nl_diff"))
        .with_columns(pl.col("nh_nl_diff").rolling_mean(window_size=20, min_samples=20).alias("nh_nl_diff_20d"))
    )
    daily.write_parquet(DAILY)

    result = {
        "purpose": "R197 HS300 new-high/new-low breadth availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": daily.height,
        "start": str(daily["date"].min()),
        "end": str(daily["date"].max()),
        "members": {
            "min": int(daily["members"].min()),
            "median": int(daily["members"].median()),
            "max": int(daily["members"].max()),
        },
        "nh20_ratio": {
            "median": round(float(daily["nh20_ratio"].median()), 6),
            "p05": round(float(daily["nh20_ratio"].quantile(0.05)), 6),
            "p95": round(float(daily["nh20_ratio"].quantile(0.95)), 6),
        },
        "nl20_ratio": {
            "median": round(float(daily["nl20_ratio"].median()), 6),
            "p05": round(float(daily["nl20_ratio"].quantile(0.05)), 6),
            "p95": round(float(daily["nl20_ratio"].quantile(0.95)), 6),
        },
        "nh_nl_diff": {
            "median": round(float(daily["nh_nl_diff"].median()), 6),
            "min": round(float(daily["nh_nl_diff"].min()), 6),
            "max": round(float(daily["nh_nl_diff"].max()), 6),
        },
        "recent": [
            {k: (round(float(v), 6) if isinstance(v, float) else str(v) if k == "date" else v)
             for k, v in r.items()}
            for r in daily.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
