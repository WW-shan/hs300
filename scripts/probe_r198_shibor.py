# -*- coding: utf-8 -*-
"""R198 probe: SHIBOR 3M availability (no returns inspected)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r198_shibor_probe.json"
CACHE = DATA / "research" / "macro" / "shibor.parquet"


def main() -> int:
    raw = pl.read_parquet(CACHE)
    frame = (
        raw.select([pl.col("日期").alias("date"), pl.col("3M-定价").alias("shibor_3m")])
        .with_columns(pl.col("date").str.to_date())
        .filter(pl.col("shibor_3m").is_not_null())
        .sort("date")
    )
    study = frame.filter((pl.col("date") >= pl.date(2018, 2, 1)) & (pl.col("date") <= pl.date(2026, 8, 31)))
    values = study["shibor_3m"]
    result = {
        "purpose": "R198 SHIBOR 3M availability probe (no returns inspected)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "data/research/macro/shibor.parquet (AkShare shibor)",
        "rows": frame.height,
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "study_window_2018_02_2026_08": {
            "rows": study.height,
            "min": round(float(values.min()), 4),
            "p25": round(float(values.quantile(0.25)), 4),
            "median": round(float(values.median()), 4),
            "p75": round(float(values.quantile(0.75)), 4),
            "max": round(float(values.max()), 4),
        },
        "recent": [
            {"date": str(r["date"]), "shibor_3m": round(float(r["shibor_3m"]), 4)}
            for r in frame.tail(5).iter_rows(named=True)
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
