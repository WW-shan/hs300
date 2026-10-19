#!/usr/bin/env python3
"""R203: extend HS300 turnover-rate history back through 2017.

The existing R201 turnover cache starts in 2018 because the historical
membership snapshots start there. R202's only failed gate was signal coverage
(88.63% < 90%), caused by the 12-month expanding z-score warmup. This script
fetches the missing 2017 membership and turnover history without inspecting
strategy returns, then writes a compact monthly scan consumed by the normal
ext-data pipeline.

Raw BaoStock caches stay under data/research and are not committed. The
committed evidence is the coverage/metadata JSON and the monthly signal rows.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "research" / "r203_turnover"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank"
    / "evidence"
)
MEMBERSHIP = CACHE / "monthly_snapshots_2017.parquet"
DAILY_2017 = CACHE / "daily_turnover_2017.parquet"
COMBINED = CACHE / "daily_turnover_2017_2026.parquet"
R201_DAILY = DATA / "research" / "r201_turnover" / "daily.parquet"
OUT = EVIDENCE / "r203_turnover_scan.json"
START = date(2017, 1, 1)
END = date(2017, 12, 31)


def _month_starts() -> list[date]:
    return [date(2017, month, 1) for month in range(1, 13)]


def _to_symbol(code: str) -> str | None:
    if code.startswith("sh."):
        return code[3:] + ".SH"
    if code.startswith("sz."):
        return code[3:] + ".SZ"
    return None


def _fetch_membership() -> pl.DataFrame:
    if MEMBERSHIP.exists() and MEMBERSHIP.stat().st_size:
        return pl.read_parquet(MEMBERSHIP)
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    rows: list[dict] = []
    try:
        for query in _month_starts():
            rs = bs.query_hs300_stocks(date=query.isoformat())
            if rs.error_code != "0":
                raise RuntimeError(f"membership {query}: {rs.error_msg}")
            count = 0
            while rs.next():
                update_date, code, name = rs.get_row_data()
                symbol = _to_symbol(code)
                if symbol:
                    rows.append({
                        "snapshot_date": query,
                        "update_date": update_date,
                        "symbol": symbol,
                        "name": name,
                    })
                    count += 1
            print(f"membership {query}: {count}", flush=True)
    finally:
        bs.logout()
    frame = pl.DataFrame(rows).with_columns([
        pl.col("snapshot_date").cast(pl.Date),
        pl.col("update_date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
    ]).unique(subset=["snapshot_date", "symbol"], keep="last").sort(["snapshot_date", "symbol"])
    CACHE.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(MEMBERSHIP)
    return frame


def _fetch_daily(membership: pl.DataFrame) -> pl.DataFrame:
    if DAILY_2017.exists() and DAILY_2017.stat().st_size:
        return pl.read_parquet(DAILY_2017)
    import baostock as bs

    symbols = sorted(membership["symbol"].unique().to_list())
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    rows: list[dict] = []
    try:
        for i, symbol in enumerate(symbols, 1):
            code = ("sh." if symbol.endswith(".SH") else "sz.") + symbol[:6]
            rs = bs.query_history_k_data_plus(
                code,
                "date,code,turn,tradestatus",
                start_date=START.isoformat(),
                end_date=END.isoformat(),
                frequency="d",
                adjustflag="3",
            )
            if rs.error_code != "0":
                print(f"skip {symbol}: {rs.error_msg}", flush=True)
                continue
            while rs.next():
                day, _code, turn, status = rs.get_row_data()
                if status != "1" or not turn:
                    continue
                try:
                    value = float(turn)
                except ValueError:
                    continue
                if value > 0:
                    rows.append({"date": date.fromisoformat(day), "symbol": symbol, "turnover_rate": value})
            if i % 25 == 0 or i == len(symbols):
                print(f"daily {i}/{len(symbols)} symbols, rows={len(rows)}", flush=True)
    finally:
        bs.logout()
    frame = pl.DataFrame(rows).with_columns([
        pl.col("date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
        pl.col("turnover_rate").cast(pl.Float64),
    ]).sort(["symbol", "date"])
    CACHE.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(DAILY_2017)
    return frame


def _daily_2017(membership: pl.DataFrame, raw: pl.DataFrame) -> pl.DataFrame:
    trading_dates = pl.DataFrame({"date": raw["date"].unique().sort()})
    snapshots = membership.select(pl.col("snapshot_date")).unique().sort("snapshot_date")
    date_map = trading_dates.join_asof(
        snapshots, left_on="date", right_on="snapshot_date", strategy="backward"
    )
    mapped = raw.join(date_map, on="date", how="left").join(
        membership, on=["snapshot_date", "symbol"], how="inner"
    )
    return (
        mapped.group_by("date")
        .agg([
            pl.len().alias("members"),
            pl.col("turnover_rate").median().alias("turnover_median"),
        ])
        .filter(pl.col("members") >= 100)
        .sort("date")
    )


def _combined_daily(new: pl.DataFrame) -> pl.DataFrame:
    if not R201_DAILY.exists():
        raise RuntimeError(f"R201 daily cache missing: {R201_DAILY}")
    old = pl.read_parquet(R201_DAILY).select(["date", "turnover_median"])
    combined = pl.concat([
        new.select(["date", "turnover_median"]),
        old.select(["date", "turnover_median"]),
    ]).unique(subset=["date"], keep="last").sort("date")
    return combined.with_columns(
        pl.col("turnover_median").rolling_mean(window_size=20, min_samples=15).alias("turnover_20d")
    ).filter(pl.col("turnover_20d").is_not_null())


def _monthly(daily: pl.DataFrame) -> pl.DataFrame:
    daily = daily.with_columns(
        (pl.col("turnover_20d") - pl.col("turnover_20d").shift(20)).alias("hs300_turnover_chg_20d")
    )
    return (
        daily.with_columns([
            pl.col("date").dt.year().alias("year"),
            pl.col("date").dt.month().alias("month"),
        ])
        .group_by(["year", "month"])
        .agg([
            pl.col("date").max().alias("date"),
            pl.col("turnover_20d").last().alias("turnover_20d"),
            pl.col("hs300_turnover_chg_20d").last().alias("hs300_turnover_chg_20d"),
        ])
        .sort(["year", "month"])
        .with_columns(pl.int_range(1, pl.len() + 1).alias("_n"))
        .with_columns(
            pl.when(pl.col("_n") >= 12)
            .then(pl.col("turnover_20d").cum_sum() / pl.col("_n"))
            .otherwise(None)
            .alias("_mean")
        )
        .with_columns(
            pl.when(pl.col("_n") >= 12)
            .then(
                ((pl.col("turnover_20d") ** 2).cum_sum() / pl.col("_n") - pl.col("_mean") ** 2)
                .clip(lower_bound=0.0)
                .sqrt()
            )
            .otherwise(None)
            .alias("_std")
        )
        .with_columns(
            pl.when((pl.col("_n") >= 12) & (pl.col("_std") > 0))
            .then((pl.col("turnover_20d") - pl.col("_mean")) / pl.col("_std"))
            .otherwise(None)
            .alias("hs300_turnover_z_ext2017")
        )
        .select(["date", "turnover_20d", "hs300_turnover_z_ext2017", "hs300_turnover_chg_20d"])
    )


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    membership = _fetch_membership()
    raw = _fetch_daily(membership)
    new_daily = _daily_2017(membership, raw)
    combined = _combined_daily(new_daily)
    combined.write_parquet(COMBINED)
    monthly = _monthly(combined)
    if monthly.is_empty():
        raise RuntimeError("monthly turnover scan is empty")
    records = []
    for row in monthly.iter_rows(named=True):
        records.append({
            "date": row["date"].isoformat(),
            "turnover_20d": round(float(row["turnover_20d"]), 6),
            "hs300_turnover_z_ext2017": (
                round(float(row["hs300_turnover_z_ext2017"]), 6)
                if row["hs300_turnover_z_ext2017"] is not None else None
            ),
            "hs300_turnover_chg_20d": (
                round(float(row["hs300_turnover_chg_20d"]), 6)
                if row["hs300_turnover_chg_20d"] is not None else None
            ),
        })
    coverage_days = new_daily.height
    trading_days = raw["date"].n_unique()
    full_signal = [r for r in records if date.fromisoformat(r["date"]) >= date(2018, 1, 1)]
    signal_count = sum(1 for r in full_signal if r["hs300_turnover_z_ext2017"] is not None)
    result = {
        "purpose": "R203 extend HS300 turnover-rate history through 2017",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "BaoStock query_hs300_stocks + query_history_k_data_plus turn",
        "membership": {
            "snapshots": membership["snapshot_date"].n_unique(),
            "symbols": membership["symbol"].n_unique(),
            "start": str(membership["snapshot_date"].min()),
            "end": str(membership["snapshot_date"].max()),
            "update_after_snapshot": int((membership["update_date"] > membership["snapshot_date"]).sum()),
        },
        "daily_2017": {
            "trading_days": trading_days,
            "covered_days": coverage_days,
            "coverage": round(coverage_days / trading_days, 6) if trading_days else 0.0,
            "min_members": int(new_daily["members"].min()) if new_daily.height else 0,
            "median_members": int(new_daily["members"].median()) if new_daily.height else 0,
        },
        "combined": {
            "start": str(combined["date"].min()),
            "end": str(combined["date"].max()),
            "days": combined.height,
        },
        "signal_coverage_2018_2026": {
            "months": len(full_signal),
            "signal_months": signal_count,
            "coverage": round(signal_count / len(full_signal), 6) if full_signal else 0.0,
            "first_signal_date": next(
                (r["date"] for r in records if r["hs300_turnover_z_ext2017"] is not None), None
            ),
        },
        "method": {
            "turnover_20d": "20-day rolling mean of daily median member turnover_rate, min_samples=15",
            "zscore": "expanding z-score from monthly turnover_20d, minimum 12 months",
            "effective_date": "month-end + 1 day; PIT membership uses latest snapshot on or before each date",
        },
        "monthly_records": records,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["membership", "daily_2017", "combined", "signal_coverage_2018_2026"]}, ensure_ascii=False, indent=2))
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
