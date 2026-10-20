#!/usr/bin/env python3
"""Build the R255 date-constant HS300 200-day trend entry gate."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_market_regime"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "hs300_above_ma200"
INDEX_SYMBOL = "000300.SH"
WINDOW = 200


def _index_gate(data_dir: Path) -> pl.DataFrame:
    paths = sorted((data_dir / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("index daily data is missing")
    return _build_gate(
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .filter(pl.col("symbol") == INDEX_SYMBOL)
        .select([pl.col("date").cast(pl.Date), pl.col("close").cast(pl.Float64)])
        .collect()
    )


def _build_gate(index: pl.DataFrame) -> pl.DataFrame:
    return (
        index.unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns(pl.col("close").rolling_mean(WINDOW, min_samples=WINDOW).alias("_ma200"))
        .with_columns(
            pl.when(pl.col("_ma200").is_not_null())
            .then((pl.col("close") > pl.col("_ma200")).cast(pl.Float64))
            .otherwise(None)
            .alias(FIELD)
        )
        .select(["date", FIELD])
        .sort("date")
    )


def _component_grid(data_dir: Path) -> pl.DataFrame:
    paths = sorted(
        (data_dir / "ext_data" / BASE_CONFIG_ID / "timeseries").glob("date=*/part.parquet")
    )
    if not paths:
        raise RuntimeError(
            "base alt_hs300 timeseries is missing; run build_alt_factor_data.py first"
        )
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date"])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
        .unique(subset=["symbol", "date"], keep="last")
    )


def _broadcast_gate(grid: pl.DataFrame, gate: pl.DataFrame) -> pl.DataFrame:
    return (
        grid.select(["symbol", "date"]).join(gate, on="date", how="left").sort(["date", "symbol"])
    )


def _factor_daily(data_dir: Path) -> pl.DataFrame:
    return _broadcast_gate(_component_grid(data_dir), _index_gate(data_dir))


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(
        ExtConfig(
            id=CONFIG_ID,
            label="沪深300趋势闸门",
            mode="timeseries",
            fields=[ExtField(FIELD, "float", "收盘高于200日均线")],
            description="R255研究因子：000300.SH 当日收盘高于其200日简单移动平均为1，否则为0；日期常量广播到股票，只限制新开仓，T+1开盘执行。",
        )
    )


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
    factor = _factor_daily(DATA)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    valid = factor.filter(pl.col(FIELD).is_not_null())
    by_date = valid.group_by("date").agg(
        pl.col(FIELD).min().alias("min"),
        pl.col(FIELD).max().alias("max"),
        pl.len().alias("rows"),
    )
    payload = {
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "index_symbol": INDEX_SYMBOL,
        "window": WINDOW,
        "partitions": partitions,
        "factor_rows": factor.height,
        "valid_rows": valid.height,
        "valid_dates": by_date.height,
        "first_valid_date": str(valid["date"].min()),
        "last_date": str(factor["date"].max()),
        "on_rows": int((valid[FIELD] == 1).sum()),
        "off_rows": int((valid[FIELD] == 0).sum()),
        "date_constant": bool(by_date.select((pl.col("min") == pl.col("max")).all()).item()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
