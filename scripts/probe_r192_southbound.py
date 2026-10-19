#!/usr/bin/env python3
"""R192 probe: southbound fund flow availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r192_southbound_probe.json"
CACHE = DATA / "research" / "r192_southbound"


def main() -> int:
    cache_path = CACHE / "southbound.parquet"
    if cache_path.exists():
        frame = pl.read_parquet(cache_path)
    else:
        raw = ak.stock_hsgt_hist_em(symbol="南向资金")
        frame = (
            pl.from_pandas(raw[["日期", "当日成交净买额"]])
            .rename({"日期": "date", "当日成交净买额": "net_buy"})
            .with_columns(pl.col("date").cast(pl.Date))
            .filter(pl.col("net_buy").is_not_null())
            .sort("date")
        )
        CACHE.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(cache_path)
    result = {
        "purpose": "R192 southbound fund flow availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "stock_hsgt_hist_em(symbol=南向资金) via AkShare",
        "rows": frame.height,
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "net_buy": {
            "median": round(float(frame["net_buy"].median()), 4),
            "min": round(float(frame["net_buy"].min()), 4),
            "max": round(float(frame["net_buy"].max()), 4),
        },
        "recent": [
            {"date": str(row["date"]), "net_buy": round(float(row["net_buy"]), 4)}
            for row in frame.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
