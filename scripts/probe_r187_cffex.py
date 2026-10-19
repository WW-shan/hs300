#!/usr/bin/env python3
"""R187 probe: CFFEX IF member position-rank availability (no returns inspected)."""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r187_cffex_probe.json"
CACHE = DATA / "research" / "r187_cffex"
START = date(2021, 1, 1)
END = date(2026, 8, 31)


def _calendar() -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )


def _month_ends(calendar: list[date]) -> list[date]:
    frame = pl.DataFrame({"date": calendar}).with_columns([
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    ])
    return (
        frame.group_by(["year", "month"])
        .agg(pl.col("date").max())
        .sort(["year", "month"])["date"]
        .to_list()
    )


def _fetch(day: date) -> dict | None:
    cache_path = CACHE / f"{day.isoformat()}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    for attempt in range(4):
        try:
            res = ak.get_cffex_rank_table(date=day.strftime("%Y%m%d"), vars_list=["IF"])
        except Exception:
            time.sleep(3 + attempt * 2)
            continue
        if isinstance(res, dict) and res:
            payload = {}
            for contract, df in res.items():
                payload[contract] = df.to_dict(orient="records")
            CACHE.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            return payload
        time.sleep(3 + attempt * 2)
    return None


def main() -> int:
    calendar = _calendar()
    month_ends = [d for d in _month_ends(calendar) if START <= d <= END]
    records = []
    failures = []
    for day in month_ends:
        payload = _fetch(day)
        if payload is None:
            failures.append(str(day))
            continue
        total_long = 0.0
        total_short = 0.0
        contracts = 0
        for contract, rows in payload.items():
            contracts += 1
            for row in rows:
                total_long += float(row.get("long_open_interest") or 0.0)
                total_short += float(row.get("short_open_interest") or 0.0)
        denom = total_long + total_short
        records.append({
            "date": str(day),
            "contracts": contracts,
            "total_long": total_long,
            "total_short": total_short,
            "net_short_ratio": round((total_short - total_long) / denom, 6) if denom else None,
        })
        print(f"{day} contracts={contracts} net_short={records[-1]['net_short_ratio']}", flush=True)
        time.sleep(0.5)

    result = {
        "purpose": "R187 CFFEX IF member position-rank availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "CFFEX get_cffex_rank_table via AkShare",
        "months_requested": len(month_ends),
        "months_ok": len(records),
        "failures": failures,
        "start": records[0]["date"] if records else None,
        "end": records[-1]["date"] if records else None,
        "net_short_ratio": {
            "median": round(float(pl.Series([r["net_short_ratio"] for r in records]).median()), 6) if records else None,
            "min": round(min(r["net_short_ratio"] for r in records), 6) if records else None,
            "max": round(max(r["net_short_ratio"] for r in records), 6) if records else None,
        },
        "monthly_records": records,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "monthly_records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
