# -*- coding: utf-8 -*-
"""R194 probe: convertible bond market sentiment availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r194_cb_probe.json"
CACHE = DATA / "research" / "r194_cb"


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    result = {"purpose": "R194 convertible bond sentiment availability probe (no returns inspected)",
              "generated_at": datetime.now().isoformat(timespec="seconds"), "sources": {}}

    # 1) CSI convertible bond index (000832) via csindex
    try:
        raw = ak.stock_zh_index_hist_csindex(symbol="000832")
        frame = pl.from_pandas(raw).select(["日期", "收盘", "成交金额"]).rename(
            {"日期": "date", "收盘": "close", "成交金额": "amount"}
        ).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        frame.write_parquet(CACHE / "cb_index.parquet")
        result["sources"]["cb_index_000832"] = {
            "ok": True, "rows": frame.height,
            "start": str(frame["date"].min()), "end": str(frame["date"].max()),
            "close_nulls": int(frame["close"].null_count()),
            "amount_nulls": int(frame["amount"].null_count()),
            "median_amount": float(frame["amount"].drop_nulls().median()) if frame["amount"].drop_nulls().len() else None,
        }
    except Exception as exc:  # noqa: BLE001
        result["sources"]["cb_index_000832"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # 2) JSL CB market index (valuation/emotion fields)
    try:
        raw = ak.bond_cb_index_jsl()
        frame = pl.from_pandas(raw).rename({"price_dt": "date"}).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        frame.write_parquet(CACHE / "cb_jsl_index.parquet")
        result["sources"]["cb_jsl_index"] = {
            "ok": True, "rows": frame.height,
            "start": str(frame["date"].min()), "end": str(frame["date"].max()),
            "columns": frame.columns,
            "premium_median": float(frame["avg_premium_rt"].drop_nulls().median()),
            "temperature_median": float(frame["temperature"].drop_nulls().median()),
        }
    except Exception as exc:  # noqa: BLE001
        result["sources"]["cb_jsl_index"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
