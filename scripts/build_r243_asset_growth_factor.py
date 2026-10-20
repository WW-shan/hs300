#!/usr/bin/env python3
"""Build the standalone R243 asset-growth factor table."""
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
CONFIG_ID = "alt_hs300_ag"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "asset_growth_yoy_low"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r243", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _asset_growth_yoy_low_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    from app.backtest.fundamentals import _statutory_deadline

    balance = pl.read_parquet(builder.DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("total_assets").cast(pl.Float64),
    ])
    financial = (
        balance.unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns((pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"))
        .with_columns([
            pl.col("total_assets").shift(4).over("symbol").alias("_total_assets_lag4"),
            pl.col("_period_index").shift(4).over("symbol").alias("_period_index_lag4"),
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_effective"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag4") == 4)
                & (pl.col("total_assets") > 0)
                & (pl.col("_total_assets_lag4") > 0)
            )
            .then((pl.col("_total_assets_lag4") / pl.col("total_assets") - 1.0).clip(-1.0, 1.0))
            .otherwise(None)
            .alias(FIELD)
        )
        .filter(pl.col(FIELD).is_not_null())
        .sort(["symbol", "_effective", "period_end"])
        .unique(subset=["symbol", "_effective"], keep="last")
        .select(["symbol", pl.col("_effective").alias("effective_date"), FIELD])
        .sort(["symbol", "effective_date"])
    )
    daily = builder._calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    return (
        daily.join_asof(
            financial.rename({"effective_date": "_effective"}),
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .select(["symbol", "date", FIELD])
    )


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300资产增长因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "总资产同比低增长(高好)")],
        description="R243研究因子：总资产相对四个季度前的反向同比增速；高值代表更低资产扩张。",
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
    factor = _asset_growth_yoy_low_daily(base, dates, symbols)
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
        "symbols": len(symbols),
        "factor_rows": factor.height,
        "valid_rows": int(factor[FIELD].is_not_null().sum()),
        "first_valid_date": str(factor.filter(pl.col(FIELD).is_not_null())["date"].min()),
        "last_date": str(factor["date"].max()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())