#!/usr/bin/env python3
"""R163: batch-fetch 2018Q1-2019Q4 top-10 free-float holders.

The per-symbol AkShare helper drops the free-float ratio and announcement
metadata.  The underlying EastMoney report exposes both, so fetch each quarter
once and keep only the historical HS300 union.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "research" / "r163_holder_batch"
MEMBERSHIP = DATA / "research" / "r157_membership" / "monthly_snapshots.parquet"
QUARTERS = ["20180331", "20180630", "20180930", "20181231", "20190331", "20190630", "20190930", "20191231"]
URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
PAGE_SIZE = 2500
RETRIES = 3
SCHEMA = {
    "symbol": pl.String,
    "period_end": pl.Date,
    "notice_date": pl.Date,
    "update_date": pl.Date,
    "announce_date": pl.Date,
    "holder_rank": pl.Int64,
    "holder_name": pl.String,
    "hold_shares": pl.Float64,
    "free_float_ratio": pl.Float64,
    "holder_type": pl.String,
}


def _date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _membership() -> pl.DataFrame:
    return pl.read_parquet(MEMBERSHIP).select([
        pl.col("query_date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
    ]).unique().sort(["query_date", "symbol"])


def _expected_symbols(membership: pl.DataFrame, quarter: str) -> set[str]:
    quarter_end = datetime.strptime(quarter, "%Y%m%d").date()
    eligible = membership.filter(pl.col("query_date") <= quarter_end)
    if eligible.is_empty():
        return set()
    latest = eligible["query_date"].max()
    return set(eligible.filter(pl.col("query_date") == latest)["symbol"].to_list())


def _request_page(quarter: str, page: int) -> dict:
    period = f"{quarter[:4]}-{quarter[4:6]}-{quarter[6:]}"
    params = {
        "sortColumns": "UPDATE_DATE,SECURITY_CODE,HOLDER_RANK",
        "sortTypes": "-1,1,1",
        "pageSize": str(PAGE_SIZE),
        "pageNumber": str(page),
        "reportName": "RPT_F10_EH_FREEHOLDERS",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f"(END_DATE='{period}')",
    }
    last_error = None
    for attempt in range(RETRIES + 1):
        try:
            response = requests.get(URL, params=params, timeout=60)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result")
            if not result:
                raise RuntimeError(f"empty API result: {str(payload)[:200]}")
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < RETRIES:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"{quarter} page {page} failed: {type(last_error).__name__}: {last_error}")


def _fetch_quarter(quarter: str, code_to_symbol: dict[str, str]) -> tuple[pl.DataFrame, dict]:
    first = _request_page(quarter, 1)
    pages = int(first["pages"])
    rows = list(first.get("data") or [])
    for page in range(2, pages + 1):
        result = _request_page(quarter, page)
        rows.extend(result.get("data") or [])
        time.sleep(0.08)
    period_end = datetime.strptime(quarter, "%Y%m%d").date()
    out = []
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").zfill(6)
        symbol = code_to_symbol.get(code)
        if symbol is None:
            continue
        rank = row.get("HOLDER_RANK")
        if rank is None or int(rank) > 10:
            continue
        notice = _date(row.get("NOTICE_DATE"))
        update = _date(row.get("UPDATE_DATE"))
        available = max((day for day in (notice, update) if day is not None), default=None)
        out.append({
            "symbol": symbol,
            "period_end": period_end,
            "notice_date": notice,
            "update_date": update,
            "announce_date": available,
            "holder_rank": int(rank),
            "holder_name": row.get("HOLDER_NAME"),
            "hold_shares": row.get("HOLD_NUM"),
            "free_float_ratio": row.get("FREE_HOLDNUM_RATIO"),
            "holder_type": row.get("HOLDER_TYPE"),
        })
    frame = pl.DataFrame(out, schema=SCHEMA).filter(
        pl.col("announce_date").is_not_null()
        & pl.col("free_float_ratio").is_not_null()
    )
    frame = frame.unique(
        subset=["symbol", "period_end", "holder_rank", "holder_name", "announce_date"],
        keep="last",
    ).sort(["symbol", "holder_rank"])
    stats = {
        "quarter": quarter,
        "api_rows": len(rows),
        "pages": pages,
        "target_rows": frame.height,
        "symbols": frame["symbol"].n_unique(),
    }
    return frame, stats


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    membership = _membership()
    union_symbols = sorted(membership["symbol"].unique().to_list())
    code_to_symbol = {symbol[:6]: symbol for symbol in union_symbols}
    print(f"union_symbols={len(union_symbols)} quarters={len(QUARTERS)}", flush=True)

    manifest = {"quarters": {}, "failures": {}}
    for index, quarter in enumerate(QUARTERS, start=1):
        path = CACHE / f"{quarter}.parquet"
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
            stats = {"quarter": quarter, "cached": True, "target_rows": frame.height, "symbols": frame["symbol"].n_unique()}
        else:
            try:
                frame, stats = _fetch_quarter(quarter, code_to_symbol)
                frame.write_parquet(path)
            except Exception as exc:  # noqa: BLE001
                manifest["failures"][quarter] = f"{type(exc).__name__}: {str(exc)[:300]}"
                print(f"quarter {quarter} FAILED: {manifest['failures'][quarter]}", flush=True)
                continue
        expected = _expected_symbols(membership, quarter)
        covered = set(frame["symbol"].unique().to_list())
        matched = len(expected & covered)
        stats.update({
            "expected_symbols": len(expected),
            "covered_expected": matched,
            "coverage": round(matched / len(expected), 6) if expected else None,
            "missing_sample": sorted(expected - covered)[:10],
        })
        manifest["quarters"][quarter] = stats
        print(f"q{index}/{len(QUARTERS)} {quarter} rows={stats['target_rows']} symbols={stats['symbols']} coverage={stats['coverage']}", flush=True)

    manifest["failure_count"] = len(manifest["failures"])
    manifest["all_quarters_fetched"] = len(manifest["quarters"]) == len(QUARTERS)
    manifest["min_coverage"] = min(
        (item["coverage"] for item in manifest["quarters"].values() if item["coverage"] is not None),
        default=None,
    )
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("manifest:", json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0 if manifest["all_quarters_fetched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
