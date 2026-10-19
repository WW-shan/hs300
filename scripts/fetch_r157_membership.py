"""R157 step 1: fetch 2018-2020 monthly HS300 membership snapshots via BaoStock."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "research" / "r157_membership"
START = date(2018, 1, 1)
END = date(2020, 12, 31)


def _months() -> list[date]:
    out = []
    y, m = START.year, START.month
    while (y, m) <= (END.year, END.month):
        out.append(date(y, m, 1))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def main() -> int:
    import baostock as bs

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "monthly_snapshots.parquet"
    if path.exists() and path.stat().st_size > 0:
        frame = pl.read_parquet(path)
        print("cached:", frame.height, "rows", frame["query_date"].n_unique(), "months", flush=True)
        return 0

    r = bs.login()
    if r.error_code != "0":
        raise RuntimeError(f"login failed: {r.error_msg}")
    rows = []
    failures = {}
    try:
        for query in _months():
            rs = bs.query_hs300_stocks(date=query.isoformat())
            if rs.error_code != "0":
                failures[query.isoformat()] = rs.error_msg
                continue
            count = 0
            while rs.next():
                update_date, code, name = rs.get_row_data()
                rows.append({
                    "query_date": query,
                    "update_date": update_date,
                    "code": code,
                    "name": name,
                })
                count += 1
            print(f"{query} -> {count} rows", flush=True)
    finally:
        bs.logout()

    frame = (
        pl.DataFrame(rows)
        .with_columns([
            pl.col("query_date").cast(pl.Date),
            pl.col("update_date").cast(pl.Date),
            pl.col("code").str.extract(r"\.(\d{6})$", 1).alias("code6"),
        ])
        .with_columns(
            pl.when(pl.col("code").str.starts_with("sh."))
            .then(pl.col("code6") + pl.lit(".SH"))
            .when(pl.col("code").str.starts_with("sz."))
            .then(pl.col("code6") + pl.lit(".SZ"))
            .otherwise(None)
            .alias("symbol")
        )
        .select(["query_date", "update_date", "symbol", "name"])
        .filter(pl.col("symbol").is_not_null())
        .sort(["query_date", "symbol"])
    )
    frame.write_parquet(path)
    print("wrote:", frame.height, "rows", frame["query_date"].n_unique(), "months", "failures:", len(failures), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
