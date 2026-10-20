#!/usr/bin/env python3
"""Build the standalone R247 seven-signal accounting-quality factor table."""
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
CONFIG_ID = "alt_hs300_pf7"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "piotroski7_score"
MAX_AGE_DAYS = 500
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r247", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _piotroski7_annual(builder) -> pl.DataFrame:
    from app.backtest.fundamentals import _statutory_deadline

    income = pl.read_parquet(builder.DATA / "financials" / "income" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("revenue").cast(pl.Float64),
        pl.col("operating_costs").cast(pl.Float64),
        pl.col("net_income").cast(pl.Float64),
    ]).filter(pl.col("period_end").dt.month() == 12)
    balance = pl.read_parquet(builder.DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("total_assets").cast(pl.Float64),
        pl.col("total_liabilities").cast(pl.Float64),
    ]).filter(pl.col("period_end").dt.month() == 12)
    cash = pl.read_parquet(builder.DATA / "financials" / "cash_flow" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("net_operating_cash_flow").cast(pl.Float64),
    ]).filter(pl.col("period_end").dt.month() == 12)

    annual = (
        income.join(balance, on=["symbol", "period_end"], how="inner")
        .join(cash, on=["symbol", "period_end"], how="inner")
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns([
            pl.col("period_end").shift(1).over("symbol").alias("_prev_period"),
            pl.col("net_income").shift(1).over("symbol").alias("_prev_ni"),
            pl.col("total_assets").shift(1).over("symbol").alias("_prev_assets"),
            pl.col("total_liabilities").shift(1).over("symbol").alias("_prev_liabilities"),
            pl.col("revenue").shift(1).over("symbol").alias("_prev_revenue"),
            pl.col("operating_costs").shift(1).over("symbol").alias("_prev_costs"),
        ])
        .with_columns(
            (pl.col("period_end").dt.year() - pl.col("_prev_period").dt.year()).alias("_year_gap")
        )
        .with_columns([
            pl.when((pl.col("total_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("net_income") / pl.col("total_assets")).otherwise(None).alias("_roa"),
            pl.when((pl.col("_prev_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("_prev_ni") / pl.col("_prev_assets")).otherwise(None).alias("_prev_roa"),
            pl.when((pl.col("total_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("total_liabilities") / pl.col("total_assets")).otherwise(None).alias("_leverage"),
            pl.when((pl.col("_prev_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("_prev_liabilities") / pl.col("_prev_assets")).otherwise(None).alias("_prev_leverage"),
            pl.when((pl.col("revenue") > 0) & (pl.col("_year_gap") == 1))
            .then((pl.col("revenue") - pl.col("operating_costs")) / pl.col("revenue")).otherwise(None).alias("_gross_margin"),
            pl.when((pl.col("_prev_revenue") > 0) & (pl.col("_year_gap") == 1))
            .then((pl.col("_prev_revenue") - pl.col("_prev_costs")) / pl.col("_prev_revenue")).otherwise(None).alias("_prev_gross_margin"),
            pl.when((pl.col("total_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("revenue") / pl.col("total_assets")).otherwise(None).alias("_asset_turnover"),
            pl.when((pl.col("_prev_assets") > 0) & (pl.col("_year_gap") == 1))
            .then(pl.col("_prev_revenue") / pl.col("_prev_assets")).otherwise(None).alias("_prev_asset_turnover"),
        ])
        .with_columns([
            (pl.col("_roa") > 0).cast(pl.Int8).alias("_c1"),
            (pl.col("net_operating_cash_flow") > 0).cast(pl.Int8).alias("_c2"),
            (pl.col("_roa") > pl.col("_prev_roa")).cast(pl.Int8).alias("_c3"),
            (pl.col("net_operating_cash_flow") > pl.col("net_income")).cast(pl.Int8).alias("_c4"),
            (pl.col("_leverage") < pl.col("_prev_leverage")).cast(pl.Int8).alias("_c5"),
            (pl.col("_gross_margin") > pl.col("_prev_gross_margin")).cast(pl.Int8).alias("_c6"),
            (pl.col("_asset_turnover") > pl.col("_prev_asset_turnover")).cast(pl.Int8).alias("_c7"),
        ])
        .with_columns(
            sum([pl.col(f"_c{index}") for index in range(1, 8)]).cast(pl.Int8).alias("_score")
        )
        .with_columns(
            pl.when(
                pl.all_horizontal([pl.col(f"_c{index}").is_not_null() for index in range(1, 8)])
            )
            .then(pl.col("_score") / 7.0)
            .otherwise(None)
            .alias(FIELD)
        )
        .filter(pl.col(FIELD).is_not_null())
        .with_columns(
            (pl.col("period_end")
             .map_elements(_statutory_deadline, return_dtype=pl.Date)
             .dt.offset_by("3d"))
            .alias("effective_date")
        )
        .select(["symbol", "period_end", "effective_date", FIELD])
        .sort(["symbol", "effective_date"])
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )
    return annual


def _piotroski7_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    annual = _piotroski7_annual(builder)
    calendar_builder = builder if hasattr(builder, "_calendar_frame") else builder._load_base_builder()
    daily = calendar_builder._calendar_frame(dates, symbols).select(["symbol", "date"]).sort(["symbol", "date"])
    joined = daily.join_asof(
        annual.rename({"effective_date": "_effective"}),
        left_on="date",
        right_on="_effective",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).with_columns((pl.col("date") - pl.col("period_end")).dt.total_days().alias("_age_days"))
    return joined.with_columns(
        pl.when(
            pl.col("_age_days").is_not_null()
            & pl.col("_age_days").is_between(0, MAX_AGE_DAYS, closed="both")
        )
        .then(pl.col(FIELD))
        .otherwise(None)
        .alias(FIELD)
    ).select(["symbol", "date", FIELD])


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300\u4e03\u9879\u4f1a\u8ba1\u8d28\u91cf\u56e0\u5b50",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "\u4e03\u9879\u4f1a\u8ba1\u8d28\u91cf\u5206\u6570(\u9ad8\u597d)")],
        description="R247\u7814\u7a76\u56e0\u5b50\uff1a\u76c8\u5229\u3001\u73b0\u91d1\u6d41\u3001\u6760\u6746\u3001\u6bdb\u5229\u7387\u548c\u8d44\u4ea7\u5468\u8f6c\u7684\u4e03\u9879\u5e74\u5ea6\u6539\u5584\u7b49\u6743\u5206\u6570\uff1b\u5e74\u62a5\u6cd5\u5b9a\u622a\u6b62\u65e5\u540e\u751f\u6548\uff0c\u8d85\u8fc7500\u5929\u7f6e\u7a7a\u3002",
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
    factor = _piotroski7_daily(base, dates, symbols)
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
