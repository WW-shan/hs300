# 输入说明: R94 — 中国国债 10Y-2Y 期限利差历史覆盖审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r94_term_spread_probe.py
# 结果 JSON: 同目录 r94_term_spread_probe.json。原始缓存复用 data/research/macro/bond_10y.parquet。
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
BOND_CACHE = DATA / "research" / "macro" / "bond_10y.parquet"
START = date(2018, 1, 1)
END = date(2026, 10, 14)


def main() -> int:
    raw = pl.read_parquet(BOND_CACHE).with_columns([
        pl.col("日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("中国国债收益率10年").cast(pl.Float64, strict=False).alias("y10"),
        pl.col("中国国债收益率2年").cast(pl.Float64, strict=False).alias("y2"),
    ]).filter(
        pl.col("日期").is_not_null()
        & pl.col("y10").is_not_null()
        & pl.col("y2").is_not_null()
    ).with_columns(
        (pl.col("y10") - pl.col("y2")).alias("term_spread_10y_2y")
    ).sort("日期").unique(subset=["日期"], keep="last")
    monthly = (
        raw.with_columns(pl.col("日期").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").agg([
            pl.col("日期").max().alias("date"),
            pl.col("term_spread_10y_2y").last().alias("term_spread_10y_2y"),
        ]).sort("date")
        .with_columns(
            (pl.col("term_spread_10y_2y") - pl.col("term_spread_10y_2y").shift(3)).alias("term_spread_10y_2y_chg3m")
        )
    )
    analysis = monthly.filter((pl.col("date") >= date(2021, 6, 1)) & (pl.col("date") <= END))
    source_ok = bool(
        raw.height >= 1500
        and monthly.height >= 80
        and analysis.height >= 60
        and analysis["term_spread_10y_2y"].is_not_null().sum() >= 60
        and analysis["term_spread_10y_2y_chg3m"].is_not_null().sum() >= 56
        and raw["日期"].max() >= date(2026, 9, 30)
    )
    result = {
        "purpose": "R94 China 10Y-2Y term-spread history audit; no returns inspected",
        "window": {"start": str(START), "end": str(END)},
        "daily_rows": raw.height,
        "monthly_rows": monthly.height,
        "date_min": str(raw["日期"].min()) if raw.height else None,
        "date_max": str(raw["日期"].max()) if raw.height else None,
        "analysis_months": analysis.height,
        "term_spread": {
            "min": round(float(analysis["term_spread_10y_2y"].min()), 6) if analysis.height else None,
            "median": round(float(analysis["term_spread_10y_2y"].median()), 6) if analysis.height else None,
            "max": round(float(analysis["term_spread_10y_2y"].max()), 6) if analysis.height else None,
            "chg3m_nonnull": int(analysis["term_spread_10y_2y_chg3m"].is_not_null().sum()) if analysis.height else 0,
        },
        "source_ok": source_ok,
        "D1": source_ok,
        "decision": "proceed to R94 pre-registration" if source_ok else "data gap; do not compute R94 signals",
        "caveat": "Term spread is China 10Y treasury yield minus 2Y treasury yield at month-end. No equity return inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r94_term_spread_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
