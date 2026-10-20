#!/usr/bin/env python3
"""Build the standalone R251 capex-intensity-improvement factor table."""
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
CONFIG_ID = "alt_hs300_capexyoy"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "capex_intensity_improvement_yoy"
MAX_AGE_DAYS = 500
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r251", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _capex_improvement_annual(builder) -> pl.DataFrame:
    from app.backtest.fundamentals import _statutory_deadline

    capex_paths = sorted((builder.DATA / "research" / "r86_intangible").glob("*.parquet"))
    if not capex_paths:
        raise RuntimeError(
            "R86 annual capex cache is missing; run "
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r86_intangible_investment.py"
        )
    capex = pl.concat(
        [pl.read_parquet(path) for path in capex_paths],
        how="diagonal_relaxed",
    ).select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.Date),
        pl.col("notice_date").cast(pl.Date),
        pl.col("capex").cast(pl.Float64),
    ])
    income = pl.read_parquet(builder.DATA / "financials" / "income" / "part.parquet").select([
        pl.col("symbol").cast(pl.String),
        pl.col("period_end").cast(pl.String).str.to_date(),
        pl.col("revenue").cast(pl.Float64),
    ])
    annual = (
        capex.join(income, on=["symbol", "period_end"], how="inner")
        .filter((pl.col("period_end").dt.month() == 12) & pl.col("notice_date").is_not_null())
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns([
            pl.col("period_end").shift(1).over("symbol").alias("_previous_period"),
            pl.col("capex").shift(1).over("symbol").alias("_previous_capex"),
            pl.col("revenue").shift(1).over("symbol").alias("_previous_revenue"),
        ])
        .with_columns(
            (pl.col("period_end").dt.year() - pl.col("_previous_period").dt.year()).alias("_year_gap")
        )
        .with_columns(
            pl.when(
                (pl.col("_year_gap") == 1)
                & (pl.col("revenue") > 0)
                & (pl.col("_previous_revenue") > 0)
                & pl.col("capex").is_not_null()
                & pl.col("_previous_capex").is_not_null()
            )
            .then(
                pl.col("_previous_capex") / pl.col("_previous_revenue")
                - pl.col("capex") / pl.col("revenue")
            )
            .otherwise(None)
            .alias(FIELD)
        )
        .filter(pl.col(FIELD).is_not_null() & pl.col(FIELD).is_finite())
        .with_columns([
            pl.col("period_end")
            .map_elements(_statutory_deadline, return_dtype=pl.Date)
            .dt.offset_by("3d")
            .alias("_deadline"),
            (pl.col("notice_date") + pl.duration(days=1)).alias("_notice_effective"),
        ])
        .with_columns(
            pl.max_horizontal("_deadline", "_notice_effective").alias("effective_date")
        )
        .select(["symbol", "period_end", "effective_date", FIELD])
        .sort(["symbol", "effective_date", "period_end"])
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
    )
    return annual


def _capex_improvement_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    annual = _capex_improvement_annual(builder)
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
        label="HS300资本开支强度改善因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "资本开支/营收同比下降(高好)")],
        description="R251研究因子：上一年度资本开支/营收减本年度资本开支/营收；正值表示投资强度下降，年报按公告日与法定截止日较晚者生效，超过500天置空。",
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
    factor = _capex_improvement_daily(base, dates, symbols)
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
