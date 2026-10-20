#!/usr/bin/env python3
"""Build the R261 quality-relative-strength plus low-lottery-demand factor table."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300_qlot"
BASE_CONFIG_ID = "alt_hs300"
FIELD = "quality_low_lottery_min_rank"
R257_CONFIG = "alt_hs300_qrs"
R257_FIELD = "quality_relative_strength_min_rank"
LOTTERY_WINDOW = 20
LOW_LOTTERY_FIELD = "low_max_ret_20d"
COMPONENTS = (R257_FIELD, LOW_LOTTERY_FIELD)
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
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col(field).cast(pl.Float64),
        )
    )


def _low_lottery_frame_from_prices(prices: pl.DataFrame) -> pl.DataFrame:
    normalized = (
        prices.select(["symbol", "date", "close"])
        .with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col("close").cast(pl.Float64),
        )
        .unique(subset=["symbol", "date"], keep="last")
        .sort(["symbol", "date"])
    )
    previous = pl.col("close").shift(1).over("symbol")
    daily_return = (
        pl.when((pl.col("close") > 0) & (previous > 0))
        .then(pl.col("close") / previous - 1)
        .otherwise(None)
    )
    return (
        normalized.with_columns(daily_return.alias("_daily_return"))
        .with_columns(
            pl.col("_daily_return")
            .rolling_max(
                window_size=LOTTERY_WINDOW,
                min_samples=LOTTERY_WINDOW,
            )
            .over("symbol")
            .alias("_max_ret_20d")
        )
        .with_columns((-pl.col("_max_ret_20d")).alias(LOW_LOTTERY_FIELD))
        .select(["symbol", "date", LOW_LOTTERY_FIELD])
    )


def _low_lottery_frame() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("enriched daily kline is missing")
    prices = pl.scan_parquet(
        [str(path) for path in paths], hive_partitioning=True
    ).select(["symbol", "date", "close"])
    return _low_lottery_frame_from_prices(prices.collect())


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
        grid.join(
            _factor_table(R257_CONFIG, R257_FIELD),
            on=["symbol", "date"],
            how="left",
        )
        .join(_low_lottery_frame(), on=["symbol", "date"], how="left")
        .sort(["date", "symbol"])
    )


def _membership_with_industry(base) -> pl.DataFrame:
    r253_builder = _load(R253_BUILDER, "r253_membership_builder_r261")
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
            & pl.all_horizontal(
                [pl.col(component).is_not_null() for component in COMPONENTS]
            )
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
                    (pl.col("_names") - pl.col(f"_rank_{component}"))
                    / (pl.col("_names") - 1.0)
                ).alias(f"_pct_{component}")
                for component in COMPONENTS
            ]
        )
        .with_columns(
            pl.min_horizontal(
                [pl.col(f"_pct_{component}") for component in COMPONENTS]
            ).alias(FIELD)
        )
        .select(["symbol", "date", FIELD])
    )
    return panel.select(["symbol", "date"]).join(
        ranked, on=["symbol", "date"], how="left"
    )


def _factor_daily(base) -> pl.DataFrame:
    return _min_rank_factor(_component_panel(), _membership_with_industry(base))


def _upsert_config(store: ExtConfigStore) -> None:
    store.upsert(
        ExtConfig(
            id=CONFIG_ID,
            label="HS300质量相对强度与低彩票需求因子",
            mode="timeseries",
            fields=[
                ExtField(
                    FIELD,
                    "float",
                    "质量相对强度与低20日最大单日涨幅分位(高好)",
                )
            ],
            description=(
                "R261研究因子：在历史非金融HS300成员内，"
                "对R257质量相对强度最弱一环和"
                "-max_ret_20d分别计算同日横截面分位，"
                "再取两者最小值；两个组件必须同时可用。"
            ),
        )
    )


def _write_partitions(frame: pl.DataFrame) -> int:
    out_root = DATA / "ext_data" / CONFIG_ID / "timeseries"
    out_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for part in frame.sort(["date", "symbol"]).partition_by(
        "date", maintain_order=True
    ):
        day = part["date"][0]
        target = out_root / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(part.drop("date"), target)
        count += 1
    return count


def main() -> int:
    base = _load(BASE_BUILDER, "base_builder_r261")
    factor = _factor_daily(base)
    partitions = _write_partitions(factor)
    _upsert_config(ExtConfigStore(DATA))
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    valid = factor.filter(pl.col(FIELD).is_not_null())
    payload = {
        "round": "R261",
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "components": list(COMPONENTS),
        "lottery_window": LOTTERY_WINDOW,
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
