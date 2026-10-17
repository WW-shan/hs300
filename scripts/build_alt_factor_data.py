#!/usr/bin/env python3
"""Build the experimental HS300 alternative-factor extension tables.

The research caches under ``data/research`` are deliberately not committed.
This script turns those caches into the normal ``ext_data`` contract so the
factor library, screener and strategy backtests consume the same PIT columns.

Usage (from the repository root):
    cd backend && uv run python ../scripts/build_alt_factor_data.py

Factors written:
  - ext_alt_hs300_insider_buy_20d: decayed open-market insider-buy event score
  - ext_alt_hs300_holder_top10_free_float_share: top-10 free-float holder sum
  - ext_alt_hs300_dividend_growth_ttm: TTM cash-dividend growth
  - ext_alt_hs300_equity_risk_premium: HS300 earnings yield minus 10Y CGB yield

All values are keyed by (symbol, date) and only use information whose
announcement date is strictly before the signal date.  The output is local
research data; it is intentionally ignored by Git.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.factors import ext_factors
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CONFIG_ID = "alt_hs300"
FIELDS = [
    ExtField("insider_buy_20d", "float", "董监高竞价增持事件强度(20日)"),
    ExtField("holder_top10_free_float_share", "float", "前十大流通股东持股比例合计"),
    ExtField("dividend_growth_ttm", "float", "现金分红TTM增长率"),
    ExtField("equity_risk_premium", "float", "沪深300 ERP(盈利收益率-10年国债)"),
]


def _calendar() -> tuple[list[date], list[str]]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("kline_daily_enriched 为空，无法建立交易日历")
    dates = (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    holder_files = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    # The holder cache is the deterministic historical-HS300 sample.  The
    # insider cache covers the whole market, so keeping its extra symbols would
    # multiply the extension table by ~8x without helping an HS300 strategy.
    symbols = {path.stem for path in holder_files}
    if not symbols:
        raise RuntimeError("研究缓存中没有可用股票代码")
    return dates, sorted(symbols)


def _holder_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=[
            "symbol", "period_end", "announce_date", "holder_rank", "free_float_ratio",
        ]).filter(pl.col("holder_rank") <= 10)
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    raw = raw.filter(
        pl.col("announce_date").is_not_null()
        & pl.col("period_end").is_not_null()
        & pl.col("free_float_ratio").is_not_null()
    )
    if raw.is_empty():
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "holder_top10_free_float_share": pl.Float64})
    grouped = (
        raw.group_by(["symbol", "period_end", "announce_date"])
        .agg(pl.col("free_float_ratio").sum().alias("holder_top10_free_float_share"))
        .with_columns((pl.col("announce_date") + pl.duration(days=1)).alias("effective_date"))
        .sort(["symbol", "effective_date"])
    )
    base = (
        pl.DataFrame({"date": dates})
        .join(pl.DataFrame({"symbol": symbols}), how="cross")
        .sort(["symbol", "date"])
    )
    daily = base.join_asof(
        grouped.select(["symbol", "effective_date", "holder_top10_free_float_share"]),
        left_on="date",
        right_on="effective_date",
        by="symbol",
        strategy="backward",
    )
    return daily.select(["symbol", "date", "holder_top10_free_float_share"])


def _dividend_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r89_dividend_growth").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=["symbol", "announce_date", "cash_per10", "split_multiplier"])
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if raw.is_empty():
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "dividend_growth_ttm": pl.Float64})
    raw = raw.filter(pl.col("announce_date").is_not_null()).sort(["symbol", "announce_date"])
    rows: list[dict] = []
    for symbol in symbols:
        events = raw.filter(pl.col("symbol") == symbol).to_dicts()
        if not events:
            continue
        for day in dates:
            window = [
                row for row in events
                if day - timedelta(days=730) < row["announce_date"] < day
            ]
            if not window:
                continue
            current = 0.0
            previous = 0.0
            cutoff = day - timedelta(days=365)
            for index, row in enumerate(window):
                normalizer = math.prod(
                    float(later["split_multiplier"] or 1.0)
                    for later in window[index:]
                    if later["announce_date"] <= day
                )
                cash = float(row["cash_per10"] or 0.0) / 10.0 / max(normalizer, 1e-12)
                if row["announce_date"] > cutoff:
                    current += cash
                else:
                    previous += cash
            if previous > 0:
                rows.append({
                    "symbol": symbol,
                    "date": day,
                    "dividend_growth_ttm": current / previous - 1.0,
                })
    return pl.DataFrame(rows, schema={"symbol": pl.String, "date": pl.Date, "dividend_growth_ttm": pl.Float64})


def _insider_daily(dates: list[date], symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "research" / "r90_insider_trade").glob("*.parquet"))
    frames = [
        pl.read_parquet(path, columns=["symbol", "announce_date", "direction"])
        for path in paths
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    raw = raw.filter(
        (pl.col("direction") == "buy") & pl.col("announce_date").is_not_null()
    )
    if raw.is_empty():
        return pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "insider_buy_20d": pl.Float64})
    date_values = [day.toordinal() for day in dates]
    rows: list[dict] = []
    for symbol in symbols:
        event_dates = sorted(
            raw.filter(pl.col("symbol") == symbol)["announce_date"].unique().to_list()
        )
        if not event_dates:
            continue
        score = [0.0] * len(dates)
        for announce in event_dates:
            # Strictly next trading session: announcement-day close is not tradable
            # for a signal based on an announcement that may arrive after close.
            start = 0
            target = announce.toordinal()
            while start < len(date_values) and date_values[start] <= target:
                start += 1
            for offset in range(20):
                index = start + offset
                if index >= len(date_values):
                    break
                score[index] += 1.0 - offset / 20.0
        for index, value in enumerate(score):
            if value > 0:
                rows.append({"symbol": symbol, "date": dates[index], "insider_buy_20d": value})
    return pl.DataFrame(rows, schema={"symbol": pl.String, "date": pl.Date, "insider_buy_20d": pl.Float64})


def _erp_daily(dates: list[date]) -> pl.DataFrame:
    pe_path = DATA / "research" / "r93_erp" / "hs300_pe.parquet"
    bond_path = DATA / "research" / "macro" / "bond_10y.parquet"
    if not pe_path.exists() or not bond_path.exists():
        raise RuntimeError("ERP 研究缓存缺失: r93_erp/hs300_pe.parquet 或 macro/bond_10y.parquet")
    pe = (
        pl.read_parquet(pe_path)
        .select([
            pl.col("日期").alias("date"),
            pl.col("滚动市盈率").cast(pl.Float64).alias("pe_ttm"),
        ])
        .filter(pl.col("pe_ttm") > 0)
        .sort("date")
    )
    bond = (
        pl.read_parquet(bond_path)
        .select([
            pl.col("日期").alias("date"),
            pl.col("中国国债收益率10年").cast(pl.Float64).alias("bond_10y"),
        ])
        .filter(pl.col("bond_10y").is_not_null())
        .sort("date")
    )
    base = pl.DataFrame({"date": dates}).sort("date")
    daily = (
        base.join_asof(pe, on="date", strategy="backward")
        .join_asof(bond, on="date", strategy="backward")
        .with_columns((100.0 / pl.col("pe_ttm") - pl.col("bond_10y")).alias("equity_risk_premium"))
        .select(["date", "equity_risk_premium"])
    )
    return daily


def _write_partitions(frame: pl.DataFrame) -> int:
    out_root = DATA / "ext_data" / CONFIG_ID / "timeseries"
    if out_root.exists():
        import shutil
        shutil.rmtree(out_root)
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
    dates, symbols = _calendar()
    print(f"calendar={len(dates)} symbols={len(symbols)}", flush=True)

    holder = _holder_daily(dates, symbols)
    dividend = _dividend_daily(dates, symbols)
    insider = _insider_daily(dates, symbols)
    erp = _erp_daily(dates)

    base = (
        pl.DataFrame({"date": dates})
        .join(pl.DataFrame({"symbol": symbols}), how="cross")
        .join(holder, on=["symbol", "date"], how="left")
        .join(dividend, on=["symbol", "date"], how="left")
        .join(insider, on=["symbol", "date"], how="left")
        .join(erp, on="date", how="left")
    )

    cfg = ExtConfig(
        id=CONFIG_ID,
        label="HS300另类因子",
        mode="timeseries",
        fields=FIELDS,
        description=(
            "研究实验数据: 董监高真实竞价增持事件、前十大流通股东集中度、"
            "TTM分红增长和沪深300 ERP。仅用于实验策略，不构成可部署结论。"
        ),
    )
    ExtConfigStore(DATA).upsert(cfg)
    partitions = _write_partitions(base)
    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    print(
        f"wrote rows={base.height} partitions={partitions} "
        f"factors={[spec.id for spec in ext_factors.ext_factor_specs(DATA)]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
