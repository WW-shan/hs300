#!/usr/bin/env python3
"""R186 probe: 300ETF option PCR data availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r186_pcr_probe.json"
STATS_DIR = DATA / "research" / "r171_option_skew"


def main() -> int:
    files = sorted(STATS_DIR.glob("stats_*.parquet"))
    rows = []
    for path in files:
        df = pl.read_parquet(path)
        sub = df.filter(pl.col("合约标的代码") == "510300")
        if sub.height:
            rows.append(sub.select([
                pl.col("交易日"),
                pl.col("总成交量").cast(pl.Float64),
                pl.col("认购成交量").cast(pl.Float64),
                pl.col("认沽成交量").cast(pl.Float64),
                pl.col("认沽/认购").cast(pl.Float64),
                pl.col("未平仓认购合约数").cast(pl.Float64),
                pl.col("未平仓认沽合约数").cast(pl.Float64),
                pl.col("未平仓合约总数").cast(pl.Float64),
            ]))
    frame = pl.concat(rows).sort("交易日")
    frame = frame.with_columns([
        (pl.col("认沽/认购") / 100.0).alias("pcr_volume"),
        (pl.col("未平仓认沽合约数") / pl.col("未平仓认购合约数")).alias("pcr_oi"),
    ])
    result = {
        "purpose": "R186 300ETF option PCR data availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "SSE option_daily_stats_sse via local r171 cache",
        "months": frame.height,
        "start": str(frame["交易日"].min()),
        "end": str(frame["交易日"].max()),
        "pcr_volume": {
            "nonnull": int(frame["pcr_volume"].is_not_null().sum()),
            "median": round(float(frame["pcr_volume"].median()), 6),
            "min": round(float(frame["pcr_volume"].min()), 6),
            "max": round(float(frame["pcr_volume"].max()), 6),
        },
        "pcr_oi": {
            "nonnull": int(frame["pcr_oi"].is_not_null().sum()),
            "median": round(float(frame["pcr_oi"].median()), 6),
            "min": round(float(frame["pcr_oi"].min()), 6),
            "max": round(float(frame["pcr_oi"].max()), 6),
        },
        "monthly_records": [
            {
                "date": str(row["交易日"]),
                "pcr_volume": round(float(row["pcr_volume"]), 6) if row["pcr_volume"] is not None else None,
                "pcr_oi": round(float(row["pcr_oi"]), 6) if row["pcr_oi"] is not None else None,
            }
            for row in frame.iter_rows(named=True)
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "monthly_records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
