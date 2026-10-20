#!/usr/bin/env python3
"""Build the R253 weakest-link composite factor table."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet
from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300_minrank"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "management_ocf_gm_min_rank"
COMPONENTS = (
    "management_expense_ratio_4q_low",
    "ocf_margin_4q",
    "gross_margin_yoy_improvement_q",
)
EXCLUDED_INDUSTRIES = ("银行", "非银金融")
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"


def _load_base_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data_base_r253", BASE_BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _membership_with_industry(builder) -> pl.DataFrame:
    old = pl.read_parquet(
        builder.DATA / "research" / "r157_membership" / "monthly_snapshots.parquet"
    ).select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(
        builder.DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
    ).select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    members = (
        pl.concat([old, new], how="diagonal_relaxed")
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )
    industry = load_industry_l1(builder.DATA).select(["symbol", "industry_l1"])
    return (
        members.join(industry, on="symbol", how="left")
        .filter(
            pl.col("industry_l1").is_not_null()
            & ~pl.col("industry_l1").is_in(EXCLUDED_INDUSTRIES)
        )
        .select(["effective_date", "symbol"])
        .sort(["effective_date", "symbol"])
    )


def _component_panel(builder) -> pl.DataFrame:
    paths = sorted((builder.DATA / "ext_data" / BASE_CONFIG_ID / "timeseries").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("base alt_hs300 timeseries is missing; run build_alt_factor_data.py first")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", *COMPONENTS])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
    )


def _min_rank_factor(panel: pl.DataFrame, members: pl.DataFrame) -> pl.DataFrame:
    dates = panel.select("date").unique().sort("date")
    date_map = dates.join_asof(
        members.select("effective_date").unique().sort("effective_date"),
        left_on="date",
        right_on="effective_date",
        strategy="backward",
    )
    member_daily = (
        date_map.join(members, on="effective_date", how="inner")
        .select(["date", "symbol"])
        .with_columns(pl.lit(True).alias("_eligible"))
    )
    ranked = (
        panel.join(member_daily, on=["date", "symbol"], how="left")
        .filter(
            pl.col("_eligible").fill_null(False)
            & pl.all_horizontal([pl.col(component).is_not_null() for component in COMPONENTS])
        )
        .with_columns([
            pl.col(component).rank("average", descending=True).over("date").cast(pl.Float64).alias(f"_rank_{component}")
            for component in COMPONENTS
        ])
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_names"))
        .filter(pl.col("_names") > 1)
        .with_columns([
            ((pl.col("_names") - pl.col(f"_rank_{component}")) / (pl.col("_names") - 1.0)).alias(f"_pct_{component}")
            for component in COMPONENTS
        ])
        .with_columns(
            pl.min_horizontal([pl.col(f"_pct_{component}") for component in COMPONENTS]).alias(FIELD)
        )
        .select(["symbol", "date", FIELD])
    )
    return panel.select(["symbol", "date"]).join(ranked, on=["symbol", "date"], how="left")


def _factor_daily(builder) -> pl.DataFrame:
    return _min_rank_factor(_component_panel(builder), _membership_with_industry(builder))


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300质量最弱一环因子",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "管理费用率/OCF/毛利率趋势最弱一环(高好)")],
        description="R253研究因子：在历史非金融HS300成员内，分别计算管理费用率反向、OCF利润率和毛利率同比改善的横截面分位，取三者最小值；三项必须同时可用。",
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
    factor = _factor_daily(base)
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
        "components": list(COMPONENTS),
        "partitions": partitions,
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