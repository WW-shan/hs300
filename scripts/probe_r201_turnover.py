# -*- coding: utf-8 -*-
"""R201 probe: HS300 turnover-rate level availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r201_turnover_probe.json"
CACHE = DATA / "research" / "r201_turnover"
DAILY = CACHE / "daily.parquet"


def _prices() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(p) for p in paths])
        .select(["symbol", "date", "turnover_rate"])
        .filter(pl.col("turnover_rate").is_not_null() & (pl.col("turnover_rate") > 0))
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
            pl.col("turnover_rate").median().alias("turnover_median"),
        ])
        .filter(pl.col("members") >= 100)
        .sort("date")
        .with_columns(
            pl.col("turnover_median").rolling_mean(window_size=20, min_samples=15).alias("turnover_20d")
        )
        .filter(pl.col("turnover_20d").is_not_null())
    )
    daily.write_parquet(DAILY)

    result = {
        "purpose": "R201 HS300 turnover-rate level availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": daily.height,
        "start": str(daily["date"].min()),
        "end": str(daily["date"].max()),
        "members": {
            "min": int(daily["members"].min()),
            "median": int(daily["members"].median()),
            "max": int(daily["members"].max()),
        },
        "turnover_median": {
            "min": round(float(daily["turnover_median"].min()), 4),
            "p25": round(float(daily["turnover_median"].quantile(0.25)), 4),
            "median": round(float(daily["turnover_median"].median()), 4),
            "p75": round(float(daily["turnover_median"].quantile(0.75)), 4),
            "max": round(float(daily["turnover_median"].max()), 4),
        },
        "turnover_20d": {
            "min": round(float(daily["turnover_20d"].min()), 4),
            "median": round(float(daily["turnover_20d"].median()), 4),
            "max": round(float(daily["turnover_20d"].max()), 4),
        },
        "recent": [
            {k: (round(float(v), 4) if isinstance(v, float) else str(v) if k == "date" else v)
             for k, v in r.items()}
            for r in daily.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
