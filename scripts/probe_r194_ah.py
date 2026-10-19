# -*- coding: utf-8 -*-
"""R194 probe: AH premium data availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r194_ah_probe.json"
CACHE = DATA / "research" / "r194_ah"
PAIRS = CACHE / "pairs.json"


def _to_sym(code: str) -> str:
    code = str(code).zfill(6)
    return code + (".SH" if code.startswith(("6", "9")) else ".SZ")


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    if PAIRS.exists():
        pairs = json.loads(PAIRS.read_text(encoding="utf-8"))
    else:
        spot = ak.stock_zh_ah_spot_em()
        snap = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet")
        ever = set(snap["symbol"].unique().to_list())
        pairs = []
        for row in spot.iter_rows(named=True):
            a_code = str(row["A股代码"]).zfill(6)
            sym = _to_sym(a_code)
            if sym in ever:
                pairs.append({"a_symbol": sym, "a_code": a_code,
                              "h_code": str(row["H股代码"]).zfill(5), "name": row["名称"]})
        PAIRS.write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AH pairs in HS300 ever:", len(pairs))

    # sample coverage for first 5 pairs
    samples = []
    for p in pairs[:5]:
        try:
            df = ak.stock_zh_ah_daily(symbol=p["h_code"], start_year="2018", end_year="2026")
            frame = pl.from_pandas(df).rename({"日期": "date", "收盘": "close"}).select(["date", "close"])
            frame = frame.with_columns(pl.col("date").str.to_date()).sort("date")
            samples.append({"h_code": p["h_code"], "a_symbol": p["a_symbol"],
                            "rows": frame.height, "start": str(frame["date"].min()), "end": str(frame["date"].max()),
                            "nulls": int(frame["close"].null_count())})
        except Exception as exc:  # noqa: BLE001
            samples.append({"h_code": p["h_code"], "error": f"{type(exc).__name__}: {exc}"})
        print(samples[-1], flush=True)

    # HS300 A-share close coverage for these symbols
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    a_codes = {p["a_symbol"] for p in pairs}
    a_rows = (pl.scan_parquet([str(x) for x in paths])
              .filter(pl.col("symbol").is_in(list(a_codes)))
              .select(["symbol", "date", "close"]).collect())
    a_cov = (a_rows.group_by("symbol").agg([pl.len().alias("rows"),
                                            pl.col("date").min().alias("start"),
                                            pl.col("date").max().alias("end")]))

    result = {
        "purpose": "R194 AH premium data availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pairs_in_hs300_ever": len(pairs),
        "pairs": pairs,
        "sample_hk_history": samples,
        "hs300_a_history": a_cov.sort("symbol").to_dicts()[:10],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "pairs"}, ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
