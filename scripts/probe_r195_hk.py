# -*- coding: utf-8 -*-
"""R195 probe: Hong Kong index availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r195_hk_probe.json"
CACHE = DATA / "research" / "r195_hk"
SYMBOLS = ["HSI", "HSCEI", "HSTECH"]


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    summary = {}
    for sym in SYMBOLS:
        path = CACHE / f"{sym}.parquet"
        if path.exists():
            frame = pl.read_parquet(path)
        else:
            raw = ak.stock_hk_index_daily_sina(symbol=sym)
            frame = pl.from_pandas(raw).select(["date", "close"]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            frame.write_parquet(path)
        gaps = frame.select((pl.col("date").diff().dt.total_days()).alias("gap"))["gap"].drop_nulls()
        summary[sym] = {
            "rows": frame.height,
            "start": str(frame["date"].min()),
            "end": str(frame["date"].max()),
            "close_nulls": int(frame["close"].null_count()),
            "close_zeros": int((frame["close"] == 0).sum()),
            "max_gap_days": int(gaps.max()) if gaps.len() else None,
            "median_close": round(float(frame["close"].median()), 4),
        }
        print(sym, summary[sym], flush=True)

    # HK vs A-share calendar mismatch: how many A-share month ends lack an exact HK close?
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    idx = (pl.scan_parquet([str(p) for p in paths])
           .filter(pl.col("symbol") == "000300.SH")
           .select(["date"]).collect().sort("date"))
    idx = idx.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
    month_ends = idx.group_by("ym").agg(pl.col("date").max()).sort("date")["date"].to_list()
    hsi_dates = pl.read_parquet(CACHE / "HSI.parquet")["date"].to_list()
    exact = sum(1 for d in month_ends if d in hsi_dates)
    result = {
        "purpose": "R195 Hong Kong index availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "stock_hk_index_daily_sina via AkShare",
        "series": summary,
        "calendar": {
            "a_share_month_ends": len(month_ends),
            "exact_hsi_match": exact,
            "asof_fallback": len(month_ends) - exact,
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
