# -*- coding: utf-8 -*-
"""R196 probe: margin maintenance ratio availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r196_margin_safety_probe.json"
CACHE = DATA / "research" / "r178_margin_account" / "margin_account.parquet"


def main() -> int:
    frame = pl.read_parquet(CACHE).sort("date")
    ratio = frame["maintenance_ratio"]
    valid = ratio.drop_nulls()
    study = frame.filter((pl.col("date") >= pl.date(2018, 2, 1)) & (pl.col("date") <= pl.date(2026, 8, 31)))
    study_valid = study["maintenance_ratio"].drop_nulls()
    result = {
        "purpose": "R196 margin maintenance ratio availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "stock_margin_account_info via AkShare (R178 cache)",
        "rows": frame.height,
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "maintenance_ratio": {
            "non_null": valid.len(),
            "nulls": frame.height - valid.len(),
            "min": round(float(valid.min()), 4),
            "p25": round(float(valid.quantile(0.25)), 4),
            "median": round(float(valid.median()), 4),
            "p75": round(float(valid.quantile(0.75)), 4),
            "max": round(float(valid.max()), 4),
        },
        "study_window_2018_02_2026_08": {
            "rows": study.height,
            "non_null": study_valid.len(),
            "coverage": round(study_valid.len() / study.height, 4) if study.height else None,
            "min": round(float(study_valid.min()), 4) if study_valid.len() else None,
            "max": round(float(study_valid.max()), 4) if study_valid.len() else None,
        },
        "recent": [
            {"date": str(r["date"]), "maintenance_ratio": round(float(r["maintenance_ratio"]), 4)}
            for r in frame.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
