# -*- coding: utf-8 -*-
"""R193 probe: treasury futures availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r193_treasury_probe.json"
CACHE = DATA / "research" / "r193_treasury"

SYMBOLS = ["T0", "TF0", "TS0", "TL0"]


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    summary = {}
    for sym in SYMBOLS:
        cache_path = CACHE / f"{sym}.parquet"
        if cache_path.exists():
            frame = pl.read_parquet(cache_path)
        else:
            raw = ak.futures_main_sina(symbol=sym)
            frame = (
                pl.from_pandas(raw)
                .rename({
                    "日期": "date", "开盘价": "open", "最高价": "high",
                    "最低价": "low", "收盘价": "close", "成交量": "volume",
                    "持仓量": "open_interest", "动态结算价": "settle",
                })
                .with_columns(pl.col("date").cast(pl.Date))
                .sort("date")
            )
            frame.write_parquet(cache_path)
        close = frame["close"]
        zero = int((close == 0).sum())
        nulls = int(close.null_count())
        # gap check: max gap in calendar days between consecutive rows
        gaps = frame.select(
            (pl.col("date").diff().dt.total_days()).alias("gap")
        )["gap"].drop_nulls()
        summary[sym] = {
            "rows": frame.height,
            "start": str(frame["date"].min()),
            "end": str(frame["date"].max()),
            "close_nulls": nulls,
            "close_zeros": zero,
            "max_gap_days": int(gaps.max()) if gaps.len() else None,
            "median_volume": float(frame["volume"].median()),
        }
        print(sym, summary[sym], flush=True)

    # CFFEX rank table for T (one sample date, availability only)
    rank_ok = None
    rank_note = None
    try:
        res = ak.get_cffex_rank_table(date="20240115", vars_list=["T"])
        if isinstance(res, dict) and res:
            rank_ok = True
            contracts = list(res.keys())
            rank_note = {"contracts": contracts[:5], "rows_first": len(next(iter(res.values())))}
        else:
            rank_ok = False
            rank_note = "empty"
    except Exception as exc:  # noqa: BLE001
        rank_ok = False
        rank_note = f"{type(exc).__name__}: {exc}"

    result = {
        "purpose": "R193 treasury futures availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "sina futures_main_sina (T0/TF0/TS0/TL0) + CFFEX rank table sample",
        "series": summary,
        "cffex_rank_T": {"ok": rank_ok, "note": rank_note},
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
