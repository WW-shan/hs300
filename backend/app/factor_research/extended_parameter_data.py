"""Normalized source loaders for P3 extended factor parameter research."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl


def _require_columns(frame: pl.DataFrame, columns: Collection[str], source: Path) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{source} missing columns: {missing}")


def _read_paths(paths: Sequence[Path], columns: Collection[str]) -> pl.DataFrame:
    if not paths:
        return pl.DataFrame()
    return pl.concat(
        [pl.read_parquet(path, columns=list(columns)) for path in paths],
        how="diagonal_relaxed",
    )


def _instrument_code_map(data_dir: Path) -> pl.DataFrame:
    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pl.read_parquet(path, columns=["symbol", "code"])
    _require_columns(frame, {"symbol", "code"}, path)
    return frame.select(
        [
            pl.col("symbol").cast(pl.String),
            pl.col("code").cast(pl.String).str.zfill(6).alias("_code6"),
        ]
    ).unique(subset=["_code6"], keep="last")


def _normalize_code(expression: pl.Expr) -> pl.Expr:
    return expression.cast(pl.String).str.extract(r"(\d{6})", 1).str.zfill(6)


def _load_insider(data_dir: Path, symbols: list[str]) -> pl.DataFrame:
    root = data_dir / "research" / "r90_insider_trade"
    paths = sorted(root.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    raw = _read_paths(paths, {"symbol", "announce_date", "direction"})
    _require_columns(raw, {"symbol", "announce_date", "direction"}, root)
    return (
        raw.select(
            [
                pl.col("symbol").cast(pl.String),
                pl.col("announce_date").cast(pl.Date, strict=False).alias("date"),
                pl.col("direction").cast(pl.String),
            ]
        )
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"])
        .agg(
            [
                pl.col("direction").eq("buy").any().cast(pl.Float64).alias("buy"),
                pl.col("direction").eq("sell").any().cast(pl.Float64).alias("sell"),
            ]
        )
        .sort(["symbol", "date"])
    )


def _load_lhb(data_dir: Path, symbols: list[str], code_map: pl.DataFrame) -> pl.DataFrame:
    root = data_dir / "research" / "r43_lhb"
    detail_paths = sorted(root.glob("detail_*.parquet"))
    inst_paths = sorted(root.glob("jgmm_*.parquet"))
    if not detail_paths and not inst_paths:
        raise FileNotFoundError(root)

    details = _read_paths(
        detail_paths,
        {"代码", "上榜日", "龙虎榜净买额", "市场总成交额"},
    )
    if details.is_empty():
        details = pl.DataFrame(
            schema={
                "symbol": pl.String,
                "date": pl.Date,
                "net_buy_ratio": pl.Float64,
                "event_count": pl.Float64,
            }
        )
    else:
        _require_columns(
            details,
            {"代码", "上榜日", "龙虎榜净买额", "市场总成交额"},
            root,
        )
        details = (
            details.select(
                [
                    _normalize_code(pl.col("代码")).alias("_code6"),
                    pl.col("上榜日").cast(pl.Date, strict=False).alias("date"),
                    pl.col("龙虎榜净买额").cast(pl.Float64, strict=False).fill_null(0.0),
                    pl.col("市场总成交额").cast(pl.Float64, strict=False).fill_null(0.0),
                ]
            )
            .join(code_map, on="_code6", how="inner")
            .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
            .group_by(["symbol", "date"])
            .agg(
                [
                    pl.col("龙虎榜净买额").sum().alias("_net_buy"),
                    pl.col("市场总成交额").max().alias("_market_turnover"),
                    pl.len().cast(pl.Float64).alias("event_count"),
                ]
            )
            .with_columns(
                pl.when(pl.col("_market_turnover") > 0.0)
                .then(pl.col("_net_buy") / pl.col("_market_turnover") * 100.0)
                .otherwise(None)
                .alias("net_buy_ratio")
            )
            .select(["symbol", "date", "net_buy_ratio", "event_count"])
        )

    inst = _read_paths(
        inst_paths,
        {"代码", "上榜日期", "机构买入净额", "市场总成交额"},
    )
    if inst.is_empty():
        inst = pl.DataFrame(
            schema={
                "symbol": pl.String,
                "date": pl.Date,
                "inst_net_buy_ratio": pl.Float64,
            }
        )
    else:
        _require_columns(
            inst,
            {"代码", "上榜日期", "机构买入净额", "市场总成交额"},
            root,
        )
        inst = (
            inst.select(
                [
                    _normalize_code(pl.col("代码")).alias("_code6"),
                    pl.col("上榜日期").cast(pl.Date, strict=False).alias("date"),
                    pl.col("机构买入净额").cast(pl.Float64, strict=False).fill_null(0.0),
                    pl.col("市场总成交额").cast(pl.Float64, strict=False).fill_null(0.0),
                ]
            )
            .join(code_map, on="_code6", how="inner")
            .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
            .group_by(["symbol", "date"])
            .agg(
                [
                    pl.col("机构买入净额").sum().alias("_inst_net_buy"),
                    pl.col("市场总成交额").max().alias("_market_turnover"),
                ]
            )
            .with_columns(
                pl.when(pl.col("_market_turnover") > 0.0)
                .then(pl.col("_inst_net_buy") / pl.col("_market_turnover") * 100.0)
                .otherwise(None)
                .alias("inst_net_buy_ratio")
            )
            .select(["symbol", "date", "inst_net_buy_ratio"])
        )

    return (
        details.join(inst, on=["symbol", "date"], how="full", coalesce=True)
        .with_columns(
            [
                pl.col("event_count").fill_null(0.0),
                pl.col("net_buy_ratio").fill_null(0.0),
                pl.col("inst_net_buy_ratio").fill_null(0.0),
            ]
        )
        .sort(["symbol", "date"])
    )


def _load_repurchase(
    data_dir: Path,
    symbols: list[str],
    code_map: pl.DataFrame,
) -> pl.DataFrame:
    path = data_dir / "research" / "external_r39" / "repurchase.parquet"
    if not path.exists():
        raise FileNotFoundError(path)
    raw = pl.read_parquet(path, columns=["股票代码", "最新公告日期"])
    _require_columns(raw, {"股票代码", "最新公告日期"}, path)
    return (
        raw.select(
            [
                _normalize_code(pl.col("股票代码")).alias("_code6"),
                pl.col("最新公告日期").cast(pl.Date, strict=False).alias("date"),
            ]
        )
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"])
        .agg(pl.lit(1.0).alias("event"))
        .sort(["symbol", "date"])
    )


def _load_block(
    data_dir: Path,
    symbols: list[str],
    code_map: pl.DataFrame,
) -> pl.DataFrame:
    root = data_dir / "research" / "external_r39"
    paths = sorted(root.glob("dzjy_*.parquet"))
    if not paths:
        raise FileNotFoundError(root / "dzjy_*.parquet")
    raw = _read_paths(
        paths,
        {"证券代码", "交易日期", "成交额", "折溢率", "成交额/流通市值"},
    )
    _require_columns(
        raw,
        {"证券代码", "交易日期", "成交额", "折溢率", "成交额/流通市值"},
        root,
    )
    return (
        raw.select(
            [
                _normalize_code(pl.col("证券代码")).alias("_code6"),
                pl.col("交易日期").cast(pl.Date, strict=False).alias("date"),
                pl.col("成交额").cast(pl.Float64, strict=False).fill_null(0.0).alias("_amount"),
                pl.col("折溢率").cast(pl.Float64, strict=False).alias("_premium"),
                pl.col("成交额/流通市值").cast(pl.Float64, strict=False).alias("_block_ratio"),
            ]
        )
        .join(code_map, on="_code6", how="inner")
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"])
        .agg(
            [
                pl.col("_block_ratio").sum().alias("block_ratio"),
                pl.when(pl.col("_premium").is_not_null())
                .then(pl.col("_amount") * pl.col("_premium"))
                .otherwise(0.0)
                .sum()
                .alias("_premium_num"),
                pl.when(pl.col("_premium").is_not_null())
                .then(pl.col("_amount"))
                .otherwise(0.0)
                .sum()
                .alias("_premium_den"),
            ]
        )
        .with_columns(
            pl.when(pl.col("_premium_den") > 0.0)
            .then(pl.col("_premium_num") / pl.col("_premium_den"))
            .otherwise(None)
            .alias("premium")
        )
        .select(["symbol", "date", "block_ratio", "premium"])
        .sort(["symbol", "date"])
    )


def _load_analyst(data_dir: Path, symbols: list[str]) -> pl.DataFrame:
    root = data_dir / "research" / "r47_analyst"
    paths = sorted(root.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    raw = _read_paths(
        paths,
        {"symbol", "report_date", "rating_score", "rating_change_score"},
    )
    _require_columns(
        raw,
        {"symbol", "report_date", "rating_score", "rating_change_score"},
        root,
    )
    return (
        raw.select(
            [
                pl.col("symbol").cast(pl.String),
                pl.col("report_date").cast(pl.Date, strict=False).alias("date"),
                pl.col("rating_score").cast(pl.Float64, strict=False),
                pl.col("rating_change_score").cast(pl.Float64, strict=False),
            ]
        )
        .filter(pl.col("symbol").is_in(symbols) & pl.col("date").is_not_null())
        .group_by(["symbol", "date"])
        .agg(
            [
                pl.col("rating_score").mean().alias("rating"),
                pl.col("rating_change_score").mean().alias("rating_change"),
            ]
        )
        .sort(["symbol", "date"])
    )


def load_event_panels(data_dir: Path, symbols: list[str]) -> dict[str, pl.DataFrame]:
    """Load the raw event caches used by the P3 event evaluators."""
    data_dir = Path(data_dir)
    code_map = _instrument_code_map(data_dir)
    return {
        "insider": _load_insider(data_dir, symbols),
        "lhb": _load_lhb(data_dir, symbols, code_map),
        "repurchase": _load_repurchase(data_dir, symbols, code_map),
        "block": _load_block(data_dir, symbols, code_map),
        "analyst": _load_analyst(data_dir, symbols),
    }


def _read_market_file(path: Path, value_column: str = "close") -> pl.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pl.read_parquet(path, columns=["date", value_column])
    _require_columns(frame, {"date", value_column}, path)
    return (
        frame.select(
            [
                pl.col("date").cast(pl.Date, strict=False),
                pl.col(value_column).cast(pl.Float64, strict=False),
            ]
        )
        .filter(pl.col("date").is_not_null() & pl.col(value_column).is_not_null())
        .unique(subset=["date"], keep="last")
        .sort("date")
    )


def load_market_series(data_dir: Path) -> dict[str, pl.DataFrame]:
    """Load market-level price and flow series used by P3 conditioner factors."""
    data_dir = Path(data_dir)
    research = data_dir / "research"
    treasury = research / "r193_treasury"
    hk = research / "r195_hk"
    etf_root = research / "r74_etf_flow"
    etf_paths = sorted(etf_root.glob("*.parquet"))
    if not etf_paths:
        raise FileNotFoundError(etf_root)
    etf_raw = _read_paths(etf_paths, {"基金代码", "统计日期", "基金份额"})
    _require_columns(etf_raw, {"基金代码", "统计日期", "基金份额"}, etf_root)
    etf = (
        etf_raw.select(
            [
                pl.col("统计日期").cast(pl.Date, strict=False).alias("date"),
                pl.col("基金代码").cast(pl.String),
                pl.col("基金份额").cast(pl.Float64, strict=False).alias("value"),
            ]
        )
        .filter(
            (pl.col("基金代码") == "510300")
            & pl.col("date").is_not_null()
            & (pl.col("value") > 0.0)
        )
        .unique(subset=["date"], keep="last")
        .sort("date")
        .select(["date", "value"])
    )
    return {
        "T0": _read_market_file(treasury / "T0.parquet"),
        "TF0": _read_market_file(treasury / "TF0.parquet"),
        "HSI": _read_market_file(hk / "HSI.parquet"),
        "HSCEI": _read_market_file(hk / "HSCEI.parquet"),
        "etf_510300_shares": etf,
        "qvix_300etf": _read_market_file(research / "r75_qvix" / "qvix_300etf.parquet"),
    }


def load_existing_ext_factors(
    data_dir: Path,
    factor_fields: Mapping[str, str],
) -> pl.DataFrame:
    """Load selected extension factor columns keyed by symbol/date."""
    root = Path(data_dir) / "ext_data" / "alt_hs300" / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    fields = sorted(set(factor_fields.values()))
    scan = pl.scan_parquet(
        [str(path) for path in paths],
        include_file_paths="_source_path",
    ).select(
        [
            pl.col("symbol").cast(pl.String),
            pl.col("_source_path")
            .str.extract(r"date=(\d{4}-\d{2}-\d{2})", 1)
            .str.to_date(strict=False)
            .alias("date"),
            *[pl.col(field).cast(pl.Float64).alias(field) for field in fields],
        ]
    )
    frame = scan.collect()
    return frame.select(
        [
            "symbol",
            "date",
            *[pl.col(field).alias(factor_id) for factor_id, field in factor_fields.items()],
        ]
    ).sort(["symbol", "date"])


def load_existing_market_factors(
    data_dir: Path,
    factor_fields: Mapping[str, str],
) -> pl.DataFrame:
    """Load market-level extension fields once per date."""
    wide = load_existing_ext_factors(data_dir, factor_fields)
    expressions = []
    for factor_id in factor_fields:
        expressions.append(pl.col(factor_id).drop_nulls().first().alias(factor_id))
    return wide.group_by("date").agg(expressions).sort("date")


def compare_factor_frames(
    generated: pl.DataFrame,
    existing: pl.DataFrame,
    value_column: str,
    *,
    tolerance: float = 1e-10,
    minimum_overlap: float = 0.95,
    minimum_reference_overlap: float = 0.99,
) -> dict[str, Any]:
    """Compare generated and existing factor snapshots with explicit coverage."""
    if value_column not in generated.columns:
        raise ValueError(f"generated frame missing {value_column}")
    if value_column not in existing.columns:
        raise ValueError(f"existing frame missing {value_column}")
    key_columns = ["symbol", "date"] if "symbol" in generated.columns else ["date"]
    if any(column not in existing.columns for column in key_columns):
        raise ValueError(f"key columns differ: {key_columns}")
    generated_clean = (
        generated.select([*key_columns, value_column])
        .filter(pl.col(value_column).is_not_null() & pl.col(value_column).is_finite())
        .unique(subset=key_columns, keep="last")
    )
    existing_clean = (
        existing.select([*key_columns, value_column])
        .filter(pl.col(value_column).is_not_null() & pl.col(value_column).is_finite())
        .unique(subset=key_columns, keep="last")
    )
    joined = generated_clean.join(
        existing_clean,
        on=key_columns,
        how="inner",
        suffix="_existing",
    )
    denominator = generated_clean.height
    overlap_ratio = joined.height / denominator if denominator else 1.0
    reference_denominator = existing_clean.height
    reference_overlap_ratio = (
        joined.height / reference_denominator if reference_denominator else 0.0
    )
    max_abs_error = (
        float(
            joined.select(
                (pl.col(value_column) - pl.col(f"{value_column}_existing"))
                .abs()
                .max()
                .alias("_max_abs_error")
            ).item()
        )
        if joined.height
        else None
    )
    status = (
        "pass"
        if joined.height
        and overlap_ratio >= minimum_overlap
        and reference_overlap_ratio >= minimum_reference_overlap
        and max_abs_error is not None
        and max_abs_error <= tolerance
        else "fail"
    )
    return {
        "status": status,
        "generated_rows": generated_clean.height,
        "existing_rows": existing_clean.height,
        "common_rows": joined.height,
        "key_overlap_ratio": round(float(overlap_ratio), 8),
        "reference_overlap_ratio": round(float(reference_overlap_ratio), 8),
        "null_coverage_drift": round(
            max(0.0, 1.0 - float(reference_overlap_ratio)),
            8,
        ),
        "max_abs_error": max_abs_error,
        "tolerance": tolerance,
    }
