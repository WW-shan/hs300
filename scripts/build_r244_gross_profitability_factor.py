#!/usr/bin/env python3
"""Build the standalone R244 gross-profitability factor table."""
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
CONFIG_ID = "alt_hs300_gp"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "gross_profitability"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r244", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gross_profitability_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    from app.backtest.fundamentals import _statutory_deadline

    income = pl.read_parquet(builder.DATA / "financials" / "income" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("revenue").cast(pl.Float64),
        pl.col("operating_costs").cast(pl.Float64),
    ])
    balance = pl.read_parquet(builder.DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("total_assets").cast(pl.Float64),
    ])
    financial = (
        income.unique(subset=["symbol", "period_end"], keep="last")
        .join(balance.unique(subset=["symbol", "period_end"], keep="last"), on=["symbol", "period_end"], how="inner")
        .sort(["symbol", "period_end"])
        .with_columns((pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"))
        .with_columns([
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("revenue").shift(1).over("symbol").alias("_previous_revenue"),
            pl.col("operating_costs").shift(1).over("symbol").alias("_previous_costs"),
        ])
        .with_columns(
            (pl.col("_period_index") - (pl.col("_previous_period").dt.year() * 4 + pl.col("_previous_period").dt.quarter())).alias("_quarter_gap")
        )
        .with_columns([
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("revenue"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_revenue").is_not_null())
            .then(pl.col("revenue") - pl.col("_previous_revenue"))
            .otherwise(None)
            .alias("_quarter_revenue"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("operating_costs"))
            .when((pl.col("_quarter_gap") == 1) & pl.col("_previous_costs").is_not_null())
            .then(pl.col("operating_costs") - pl.col("_previous_costs"))
            .otherwise(None)
            .alias("_quarter_costs"),
        ])
        .with_columns([
            pl.col("_quarter_revenue").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_revenue"),
            pl.col("_quarter_costs").rolling_sum(4, min_samples=4).over("symbol").alias("_ttm_costs"),
            pl.col("_quarter_revenue").is_not_null().cast(pl.Int32).rolling_sum(4, min_samples=4).over("symbol").alias("_count4"),
            pl.col("_period_index").shift(3).over("symbol").alias("_period_index_lag3"),
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_effective"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag3") == 3)
                & (pl.col("_count4") == 4)
                & (pl.col("_ttm_revenue") > 0)
                & pl.col("_ttm_costs").is_not_null()
                & (pl.col("total_assets") > 0)
            )
            .then((pl.col("_ttm_revenue") - pl.col("_ttm_costs")) / pl.col("total_assets"))
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
        label="HS300毛利生产率因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "TTM毛利/总资产(高好)")],
        description="R244研究因子：TTM营业收入减TTM营业成本后除以当前总资产，衡量资产基础创造毛利的效率。",
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
    factor = _gross_profitability_daily(base, dates, symbols)
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