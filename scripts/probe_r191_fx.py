#!/usr/bin/env python3
"""R191 probe: BOC USD/CNY central parity availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r191_fx_probe.json"
CACHE = DATA / "research" / "r191_fx"


def main() -> int:
    cache_path = CACHE / "usdcny.parquet"
    if cache_path.exists():
        frame = pl.read_parquet(cache_path)
    else:
        raw = ak.currency_boc_safe()
        frame = (
            pl.from_pandas(raw[["日期", "美元"]])
            .rename({"日期": "date", "美元": "usdcny_100"})
            .with_columns(pl.col("date").cast(pl.Date))
            .filter(pl.col("usdcny_100").is_not_null())
            .sort("date")
        )
        CACHE.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(cache_path)
    frame = frame.with_columns((pl.col("usdcny_100") / 100.0).alias("usdcny"))
    result = {
        "purpose": "R191 BOC USD/CNY central parity availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "currency_boc_safe via AkShare",
        "rows": frame.height,
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "usdcny": {
            "median": round(float(frame["usdcny"].median()), 6),
            "min": round(float(frame["usdcny"].min()), 6),
            "max": round(float(frame["usdcny"].max()), 6),
        },
        "recent": [
            {"date": str(row["date"]), "usdcny": round(float(row["usdcny"]), 6)}
            for row in frame.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
