#!/usr/bin/env python3
"""Build the R257 quality-relative-strength weakest-link factor table."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300_qrs"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "quality_relative_strength_min_rank"
R253_CONFIG = "alt_hs300_minrank"
R253_FIELD = "management_ocf_gm_min_rank"
RS_CONFIG = "alt_hs300"
RS_FIELD = "stock_rel_industry_20d"
COMPONENTS = (R253_FIELD, RS_FIELD)
BASE_BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
R253_BUILDER = ROOT / "scripts" / "build_r253_min_rank_factor.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _factor_table(config_id: str, field: str) -> pl.DataFrame:
    root = DATA / "ext_data" / config_id / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"extension factor is missing: {root}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date", field])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
    )


def _component_panel() -> pl.DataFrame:
    root = DATA / "ext_data" / BASE_CONFIG_ID / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"base extension data is missing: {root}")
    grid = (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(["symbol", "date"])
        .collect()
        .with_columns(pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date))
        .unique(subset=["symbol", "date"], keep="last")
    )
    return (
        grid.join(_factor_table(R253_CONFIG, R253_FIELD), on=["symbol", "date"], how="left")
        .join(_factor_table(RS_CONFIG, RS_FIELD), on=["symbol", "date"], how="left")
        .sort(["date", "symbol"])
    )


def _membership_with_industry(base) -> pl.DataFrame:
    r253_builder = _load(R253_BUILDER, "r253_membership_builder_r257")
    return r253_builder._membership_with_industry(base)


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
        .with_columns(
            [
                pl.col(component)
                .rank("average", descending=True)
                .over("date")
                .cast(pl.Float64)
                .alias(f"_rank_{component}")
                for component in COMPONENTS
            ]
        )
        .with_columns(pl.len().over("date").cast(pl.Float64).alias("_names"))
        .filter(pl.col("_names") > 1)
        .with_columns(
            [
                (
                    (pl.col("_names") - pl.col(f"_rank_{component}")) / (pl.col("_names") - 1.0)
                ).alias(f"_pct_{component}")
                for component in COMPONENTS
            ]
        )
        .with_columns(
            pl.min_horizontal([pl.col(f"_pct_{component}") for component in COMPONENTS]).alias(
                FIELD
            )
        )
        .select(["symbol", "date", FIELD])
    )
    return panel.select(["symbol", "date"]).join(ranked, on=["symbol", "date"], how="left")


def _factor_daily(base) -> pl.DataFrame:
    return _min_rank_factor(_component_panel(), _membership_with_industry(base))


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(
        ExtConfig(
            id=CONFIG_ID,
            label="HS300质量相对强度因子",
            mode="timeseries",
            fields=[ExtField(FIELD, "float", "质量与行业相对强度最弱一环(高好)")],
            description=(
                "R257研究因子：在历史非金融HS300成员内，对R253质量最弱一环和20日行业相对强度"
                "分别计算同日横截面分位，取两者最小值；两个组件必须同时可用。"
            ),
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
    base = _load(BASE_BUILDER, "base_builder_r257")
    factor = _factor_daily(base)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    valid = factor.filter(pl.col(FIELD).is_not_null())
    payload = {
        "round": "R257",
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "components": list(COMPONENTS),
        "partitions": partitions,
        "factor_rows": factor.height,
        "valid_rows": valid.height,
        "valid_symbols": valid["symbol"].n_unique(),
        "valid_dates": valid["date"].n_unique(),
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
