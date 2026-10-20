#!/usr/bin/env python3
"""Build the standalone R245 HS300 membership-tenure factor table."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300_tenure"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "hs300_membership_tenure_low"


def _snapshot_frame() -> pl.DataFrame:
    monthly = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("date"),
        pl.col("symbol").cast(pl.String),
    ])
    daily = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("date"),
        pl.col("symbol").cast(pl.String),
    ])
    return pl.concat([monthly, daily], how="diagonal_relaxed").unique().sort(["date", "symbol"])


def _membership_starts(snapshots: pl.DataFrame) -> pl.DataFrame:
    by_date = {
        key[0]: set(part["symbol"].to_list())
        for key, part in snapshots.group_by("date", maintain_order=True)
    }
    previous: set[str] = set()
    start_by_symbol: dict[str, date] = {}
    rows = []
    for snapshot_date in sorted(by_date):
        current = by_date[snapshot_date]
        for symbol in current - previous:
            start_by_symbol[symbol] = snapshot_date
        for symbol in current:
            rows.append({
                "snapshot_date": snapshot_date,
                "symbol": symbol,
                "start_date": start_by_symbol[symbol],
            })
        previous = current
    return pl.DataFrame(rows).sort(["snapshot_date", "symbol"])


def _tenure_daily(daily_dates: list[date], snapshots: pl.DataFrame) -> pl.DataFrame:
    starts = _membership_starts(snapshots)
    snapshot_dates = snapshots.select(pl.col("date").alias("snapshot_date")).unique().sort("snapshot_date")
    date_map = (
        pl.DataFrame({"date": daily_dates})
        .sort("date")
        .join_asof(snapshot_dates, left_on="date", right_on="snapshot_date", strategy="backward")
    )
    members = (
        date_map.join(starts, on="snapshot_date", how="inner")
        .with_columns((pl.col("date") - pl.col("start_date")).dt.total_days().clip(0).alias("_age_days"))
        .with_columns((1.0 / (1.0 + pl.col("_age_days") / 252.0)).alias(FIELD))
        .select(["symbol", "date", FIELD])
        .sort(["date", "symbol"])
    )
    return members


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300成分新近度因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "HS300连续成分新近度(高好)")],
        description="R245研究因子：当前连续HS300成分期开始后252日尺度上的新近度；新纳入或重新纳入时重置。",
    ))


def _write_partitions(frame: pl.DataFrame) -> int:
    out_root = DATA / "ext_data" / CONFIG_ID / "timeseries"
    out_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for part in frame.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
        day = part["date"][0]
        target = out_root / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(part.drop("date"), target)
        count += 1
    return count


def main() -> int:
    base_root = DATA / "ext_data" / BASE_CONFIG_ID / "timeseries"
    base_parts = sorted(base_root.glob("date=*/part.parquet"))
    if not base_parts:
        raise RuntimeError("base alt_hs300 timeseries is missing; run build_alt_factor_data.py first")
    dates = [date.fromisoformat(path.parent.name.removeprefix("date=")) for path in base_parts]
    snapshots = _snapshot_frame()
    factor = _tenure_daily(dates, snapshots)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    payload = {
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "partitions": partitions,
        "factor_rows": factor.height,
        "symbols": factor["symbol"].n_unique(),
        "first_date": str(factor["date"].min()),
        "last_date": str(factor["date"].max()),
        "min_factor": float(factor[FIELD].min()),
        "max_factor": float(factor[FIELD].max()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())