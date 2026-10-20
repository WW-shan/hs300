#!/usr/bin/env python3
"""Build the standalone R242 working-capital factor table.

The existing 106-field ``alt_hs300`` partitions are treated as read-only. This
script derives the preregistered R242 column from the same PIT financial
contract and writes an isolated ``alt_hs300_wc`` timeseries table so the
factor appears in the panel without mutating the base research table.
"""
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
CONFIG_ID = "alt_hs300_wc"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "working_capital_change_yoy_low"
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _working_capital_change_yoy_low_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    from app.backtest.fundamentals import _statutory_deadline

    balance = pl.read_parquet(builder.DATA / "financials" / "balance_sheet" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("accounts_receivable").cast(pl.Float64),
        pl.col("inventory").cast(pl.Float64),
        pl.col("accounts_payable").cast(pl.Float64),
        pl.col("total_assets").cast(pl.Float64),
    ])
    financial = (
        balance.unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.when(
                (pl.col("total_assets") > 0)
                & pl.col("accounts_receivable").is_not_null()
                & pl.col("inventory").is_not_null()
                & pl.col("accounts_payable").is_not_null()
            )
            .then(
                (
                    pl.col("accounts_receivable")
                    + pl.col("inventory")
                    - pl.col("accounts_payable")
                )
                / pl.col("total_assets")
            )
            .otherwise(None)
            .alias("_nwc_ratio"),
        ])
        .with_columns([
            pl.col("_nwc_ratio").shift(4).over("symbol").alias("_nwc_ratio_lag4"),
            pl.col("_period_index").shift(4).over("symbol").alias("_period_index_lag4"),
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_effective"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_period_index") - pl.col("_period_index_lag4") == 4)
                & pl.col("_nwc_ratio").is_not_null()
                & pl.col("_nwc_ratio_lag4").is_not_null()
            )
            .then((pl.col("_nwc_ratio_lag4") - pl.col("_nwc_ratio")).clip(-1.0, 1.0))
            .otherwise(None)
            .alias(FIELD)
        )
        .filter(pl.col(FIELD).is_not_null())
        .sort(["symbol", "_effective", "period_end"])
        .unique(subset=["symbol", "_effective"], keep="last")
        .select([
            "symbol",
            pl.col("_effective").alias("effective_date"),
            FIELD,
        ])
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
        label="HS300营运资本因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "营运资本同比改善(高好)")],
        description="R242研究因子：应收账款、存货、应付账款构建的净营运资本占总资产比率，相对四个季度前的同比改善。",
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
    factor = _working_capital_change_yoy_low_daily(base, dates, symbols)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    factor_id = ext_factors.ext_column_name(CONFIG_ID, FIELD)
    valid = factor[FIELD].is_not_null().sum()
    payload = {
        "factor_id": factor_id,
        "config_id": CONFIG_ID,
        "field": FIELD,
        "partitions": partitions,
        "symbols": len(symbols),
        "factor_rows": factor.height,
        "valid_rows": int(valid),
        "first_valid_date": str(factor.filter(pl.col(FIELD).is_not_null())["date"].min()),
        "last_date": str(factor["date"].max()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())