# -*- coding: utf-8 -*-
"""R199 probe: HS300 turnover concentration availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r199_concentration_probe.json"
CACHE = DATA / "research" / "r199_concentration"
DAILY = CACHE / "daily.parquet"


def _prices() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(p) for p in paths])
        .select(["symbol", "date", "amount"])
        .filter(pl.col("amount") > 0)
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
    prices = _prices().with_columns(
        pl.col("amount").rolling_mean(window_size=20, min_samples=15).over("symbol").alias("amount_20d")
    ).filter(pl.col("amount_20d").is_not_null())

    membership = _membership()
    trading_dates = pl.DataFrame({"date": prices["date"].unique().sort()})
    date_map = trading_dates.join_asof(
        membership.select(pl.col("snapshot_date")).unique().sort("snapshot_date"),
        left_on="date", right_on="snapshot_date", strategy="backward",
    )
    mapped = prices.join(date_map, on="date", how="left").join(
        membership, on=["snapshot_date", "symbol"], how="inner"
    )
    mapped = mapped.with_columns(
        (pl.col("amount_20d") / pl.col("amount_20d").sum().over("date")).alias("share")
    ).with_columns(
        pl.col("amount_20d").rank("ordinal", descending=True).over("date").alias("_rank")
    )
    daily = (
        mapped.group_by("date")
        .agg([
            pl.len().alias("members"),
            (pl.col("share") ** 2).sum().alias("turnover_hhi"),
            pl.when(pl.col("_rank") <= 30).then(pl.col("share")).otherwise(0.0).sum().alias("top30_amount_share"),
        ])
        .filter(pl.col("members") >= 100)
        .sort("date")
    )
    daily.write_parquet(DAILY)

    result = {
        "purpose": "R199 HS300 turnover concentration availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": daily.height,
        "start": str(daily["date"].min()),
        "end": str(daily["date"].max()),
        "members": {
            "min": int(daily["members"].min()),
            "median": int(daily["members"].median()),
            "max": int(daily["members"].max()),
        },
        "turnover_hhi": {
            "min": round(float(daily["turnover_hhi"].min()), 6),
            "median": round(float(daily["turnover_hhi"].median()), 6),
            "p95": round(float(daily["turnover_hhi"].quantile(0.95)), 6),
            "max": round(float(daily["turnover_hhi"].max()), 6),
        },
        "top30_amount_share": {
            "min": round(float(daily["top30_amount_share"].min()), 6),
            "median": round(float(daily["top30_amount_share"].median()), 6),
            "p95": round(float(daily["top30_amount_share"].quantile(0.95)), 6),
            "max": round(float(daily["top30_amount_share"].max()), 6),
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
