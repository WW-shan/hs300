# -*- coding: utf-8 -*-
"""R194: fetch H-share history for all AH pairs (cache, no returns inspected)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "research" / "r194_ah"
PAIRS = json.loads((CACHE / "pairs.json").read_text(encoding="utf-8"))
OUT = CACHE / "h_history.parquet"


def main() -> int:
    frames = []
    failures = []
    for i, p in enumerate(PAIRS, 1):
        path = CACHE / "h" / f"{p['h_code']}.parquet"
        if path.exists():
            frame = pl.read_parquet(path)
        else:
            try:
                raw = ak.stock_zh_ah_daily(symbol=p["h_code"], start_year="2018", end_year="2026")
                frame = pl.from_pandas(raw).rename({"日期": "date", "收盘": "close"}).select(["date", "close"])
                frame = frame.with_columns(pl.col("date").cast(pl.Date)).sort("date")
                path.parent.mkdir(parents=True, exist_ok=True)
                frame.write_parquet(path)
                time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                failures.append({"h_code": p["h_code"], "error": f"{type(exc).__name__}: {exc}"})
                print(f"[{i}/{len(PAIRS)}] FAIL {p['h_code']}", flush=True)
                continue
        frames.append(frame.with_columns(pl.lit(p["a_symbol"]).alias("a_symbol"),
                                         pl.lit(p["h_code"]).alias("h_code")))
        if i % 10 == 0:
            print(f"[{i}/{len(PAIRS)}] ok", flush=True)

    combined = pl.concat(frames, how="diagonal_relaxed").sort(["h_code", "date"])
    combined.write_parquet(OUT)
    print(f"rows={combined.height} pairs={combined['h_code'].n_unique()} failures={len(failures)}")
    print("date range:", combined["date"].min(), combined["date"].max())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
