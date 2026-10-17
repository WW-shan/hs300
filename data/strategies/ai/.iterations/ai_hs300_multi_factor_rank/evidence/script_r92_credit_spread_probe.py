# 输入说明: R92 — AAA 5Y 信用利差历史覆盖审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r92_credit_spread_probe.py
# 结果 JSON: 同目录 r92_credit_spread_probe.json。原始缓存 data/research/r92_credit_spread/ (不提交)。
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r92_credit_spread"
START = date(2019, 1, 1)
END = date(2026, 10, 14)
AAA_CURVE = "中债中短期票据收益率曲线(AAA)"
TREASURY_CURVE = "中债国债收益率曲线"


def _ensure_cache() -> tuple[pl.DataFrame, dict]:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures = []
    frames = []
    for year in range(START.year, END.year + 1):
        start = max(date(year, 1, 1), START)
        end = min(date(year, 12, 31), END)
        path = CACHE / f"{year}.parquet"
        if not path.exists():
            try:
                frame = ak.bond_china_yield(
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                frame.to_parquet(path, index=False)
            except Exception as exc:  # noqa: BLE001 - availability audit
                failures.append({"year": year, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
        if path.exists():
            frames.append(pl.read_parquet(path))
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if raw.height:
        raw = raw.with_columns([
            pl.col("日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
            pl.col("5年").cast(pl.Float64, strict=False),
        ]).filter(pl.col("日期").is_not_null() & pl.col("5年").is_not_null())
    audit = {
        "years": END.year - START.year + 1,
        "files": len(frames),
        "failures": failures,
        "failure_rate": round(len(failures) / (END.year - START.year + 1), 6),
        "rows": raw.height,
        "curves": sorted(raw["曲线名称"].unique().to_list()) if raw.height else [],
        "date_min": str(raw["日期"].min()) if raw.height else None,
        "date_max": str(raw["日期"].max()) if raw.height else None,
    }
    return raw, audit


def _spread(raw: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    aaa = raw.filter(pl.col("曲线名称") == AAA_CURVE).select([
        pl.col("日期").alias("date"), pl.col("5年").alias("aaa_5y")
    ])
    treasury = raw.filter(pl.col("曲线名称") == TREASURY_CURVE).select([
        pl.col("日期").alias("date"), pl.col("5年").alias("treasury_5y")
    ])
    daily = aaa.join(treasury, on="date", how="inner").with_columns(
        (pl.col("aaa_5y") - pl.col("treasury_5y")).alias("credit_spread_5y")
    ).sort("date").unique(subset=["date"], keep="last")
    month_end = (
        daily.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").agg([
            pl.col("date").max().alias("date"),
            pl.col("credit_spread_5y").last().alias("credit_spread_5y"),
        ]).sort("date")
    )
    month_end = month_end.with_columns(
        (pl.col("credit_spread_5y") - pl.col("credit_spread_5y").shift(3)).alias("credit_spread_5y_chg3m")
    )
    audit = {
        "daily_rows": daily.height,
        "daily_date_min": str(daily["date"].min()) if daily.height else None,
        "daily_date_max": str(daily["date"].max()) if daily.height else None,
        "monthly_rows": month_end.height,
        "monthly_date_min": str(month_end["date"].min()) if month_end.height else None,
        "monthly_date_max": str(month_end["date"].max()) if month_end.height else None,
        "spread_min": round(float(daily["credit_spread_5y"].min()), 6) if daily.height else None,
        "spread_median": round(float(daily["credit_spread_5y"].median()), 6) if daily.height else None,
        "spread_max": round(float(daily["credit_spread_5y"].max()), 6) if daily.height else None,
        "chg3m_nonnull": int(month_end["credit_spread_5y_chg3m"].is_not_null().sum()) if month_end.height else 0,
    }
    return month_end, audit


def main() -> int:
    raw, raw_audit = _ensure_cache()
    monthly, spread_audit = _spread(raw)
    source_ok = bool(
        raw_audit["failure_rate"] <= 0.10
        and raw_audit["rows"] > 0
        and spread_audit["daily_rows"] >= 1500
        and spread_audit["monthly_rows"] >= 60
        and spread_audit["monthly_date_max"] is not None
        and date.fromisoformat(spread_audit["monthly_date_max"]) >= date(2026, 9, 30)
        and spread_audit["chg3m_nonnull"] >= 56
    )
    result = {
        "purpose": "R92 AAA 5Y credit-spread history and monthly coverage audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "aaa_curve": AAA_CURVE, "treasury_curve": TREASURY_CURVE, "tenor": "5Y"},
        "raw": raw_audit,
        "spread": spread_audit,
        "source_ok": source_ok,
        "D1": source_ok,
        "decision": "proceed to R92 pre-registration" if source_ok else "data gap; do not compute R92 signals",
        "caveat": "Credit spread is AAA medium-term note yield minus treasury yield at the same 5Y tenor. No equity return was inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r92_credit_spread_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"raw": raw_audit, "spread": spread_audit, "source_ok": source_ok}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
