#!/usr/bin/env python3
"""Build the standalone R249 stock-level low-skewness factor table."""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300_skew"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "stock_skew_60d_low"
WINDOW = 60
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r249", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stock_skew_frame(builder) -> pl.DataFrame:
    paths = sorted((builder.DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
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
    return (
        prices.with_columns(
            pl.when((pl.col("close") > 0) & (pl.col("close").shift(1).over("symbol") > 0))
            .then(pl.col("close") / pl.col("close").shift(1).over("symbol") - 1)
            .otherwise(None)
            .alias("_return")
        )
        .with_columns(
            pl.col("_return").rolling_skew(WINDOW, min_samples=WINDOW).over("symbol").alias("_skew")
        )
        .with_columns(
            pl.when(pl.col("_skew").is_not_null() & pl.col("_skew").is_finite())
            .then(-pl.col("_skew"))
            .otherwise(None)
            .alias(FIELD)
        )
        .select(["symbol", "date", FIELD])
    )


def _stock_skew_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    calendar_builder = builder if hasattr(builder, "_calendar_frame") else builder._load_base_builder()
    grid = calendar_builder._calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return grid.join(_stock_skew_frame(builder), on=["symbol", "date"], how="left").select(["symbol", "date", FIELD])


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300个股低偏度因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "60日收益偏度反向(高好)")],
        description="R249研究因子：过去60个交易日收益偏度取反；低正偏或负偏得分更高，使用截至当日收盘的数据。",
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
    base = _load_base_builder()
    base_root = DATA / "ext_data" / BASE_CONFIG_ID / "timeseries"
    base_parts = sorted(base_root.glob("date=*/part.parquet"))
    if not base_parts:
        raise RuntimeError("base alt_hs300 timeseries is missing; run build_alt_factor_data.py first")
    dates = [date.fromisoformat(path.parent.name.removeprefix("date=")) for path in base_parts]
    symbols = (
        pl.scan_parquet([str(path) for path in base_parts])
        .select("symbol")
        .unique()
        .collect()
        .get_column("symbol")
        .sort()
        .to_list()
    )
    factor = _stock_skew_daily(base, dates, symbols)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    valid = factor.filter(pl.col(FIELD).is_not_null())
    payload = {
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "partitions": partitions,
        "symbols": len(symbols),
        "factor_rows": factor.height,
        "valid_rows": valid.height,
        "valid_symbols": valid["symbol"].n_unique(),
        "first_valid_date": str(valid["date"].min()),
        "last_date": str(factor["date"].max()),
        "factor_min": float(valid[FIELD].min()),
        "factor_median": float(valid[FIELD].median()),
        "factor_max": float(valid[FIELD].max()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())