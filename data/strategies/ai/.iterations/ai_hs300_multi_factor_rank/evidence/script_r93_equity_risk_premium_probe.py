# 输入说明: R93 — 沪深300股权风险溢价历史覆盖审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r93_equity_risk_premium_probe.py
# 结果 JSON: 同目录 r93_equity_risk_premium_probe.json。原始缓存 data/research/r93_erp/ (不提交)。
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r93_erp"
BOND_CACHE = DATA / "research" / "macro" / "bond_10y.parquet"
START = date(2018, 1, 1)
END = date(2026, 10, 14)


def _pe() -> tuple[pl.DataFrame, dict]:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "hs300_pe.parquet"
    if not path.exists():
        raw = ak.stock_index_pe_lg(symbol="沪深300")
        raw.to_parquet(path, index=False)
    frame = pl.read_parquet(path).with_columns([
        pl.col("日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("滚动市盈率").cast(pl.Float64, strict=False).alias("pe_ttm"),
    ]).filter(
        pl.col("日期").is_not_null() & pl.col("pe_ttm").is_not_null() & (pl.col("pe_ttm") > 0)
    ).sort("日期").unique(subset=["日期"], keep="last")
    audit = {
        "rows": frame.height,
        "date_min": str(frame["日期"].min()) if frame.height else None,
        "date_max": str(frame["日期"].max()) if frame.height else None,
        "pe_median": round(float(frame["pe_ttm"].median()), 6) if frame.height else None,
    }
    return frame, audit


def _bond_monthly() -> tuple[pl.DataFrame, dict]:
    raw = pl.read_parquet(BOND_CACHE).with_columns([
        pl.col("日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("中国国债收益率10年").cast(pl.Float64, strict=False).alias("bond_10y"),
    ]).filter(
        pl.col("日期").is_not_null() & pl.col("bond_10y").is_not_null()
    ).sort("日期").unique(subset=["日期"], keep="last")
    monthly = (
        raw.with_columns(pl.col("日期").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").agg([
            pl.col("日期").max().alias("date"),
            pl.col("bond_10y").last().alias("bond_10y"),
        ]).sort("date")
    )
    audit = {
        "daily_rows": raw.height,
        "monthly_rows": monthly.height,
        "date_min": str(raw["日期"].min()) if raw.height else None,
        "date_max": str(raw["日期"].max()) if raw.height else None,
    }
    return monthly, audit


def main() -> int:
    pe, pe_audit = _pe()
    bond, bond_audit = _bond_monthly()
    monthly = pe.sort("日期").join_asof(
        bond.sort("date"),
        left_on="日期",
        right_on="date",
        strategy="backward",
        check_sortedness=False,
    ).filter(
        (pl.col("日期") >= START) & (pl.col("日期") <= END)
    ).with_columns([
        (100.0 / pl.col("pe_ttm") - pl.col("bond_10y")).alias("equity_risk_premium"),
    ]).with_columns(
        (pl.col("equity_risk_premium") - pl.col("equity_risk_premium").shift(3)).alias("equity_risk_premium_chg3m")
    )
    analysis = monthly.filter((pl.col("日期") >= date(2021, 6, 1)) & (pl.col("日期") <= END))
    source_ok = bool(
        pe_audit["rows"] >= 80
        and bond_audit["daily_rows"] >= 1500
        and analysis.height >= 60
        and analysis["equity_risk_premium"].is_not_null().sum() >= 60
        and analysis["equity_risk_premium_chg3m"].is_not_null().sum() >= 56
        and date.fromisoformat(pe_audit["date_max"]) >= date(2026, 9, 30)
    )
    result = {
        "purpose": "R93 HS300 earnings-yield minus 10Y treasury history audit; no returns inspected",
        "window": {"start": str(START), "end": str(END)},
        "pe": pe_audit,
        "bond": bond_audit,
        "joined_months": monthly.height,
        "analysis_months": analysis.height,
        "erp": {
            "min": round(float(analysis["equity_risk_premium"].min()), 6) if analysis.height else None,
            "median": round(float(analysis["equity_risk_premium"].median()), 6) if analysis.height else None,
            "max": round(float(analysis["equity_risk_premium"].max()), 6) if analysis.height else None,
            "chg3m_nonnull": int(analysis["equity_risk_premium_chg3m"].is_not_null().sum()) if analysis.height else 0,
        },
        "source_ok": source_ok,
        "D1": source_ok,
        "decision": "proceed to R93 pre-registration" if source_ok else "data gap; do not compute R93 signals",
        "caveat": "ERP uses 100 / trailing PE minus 10Y China government yield in percentage points. No equity return inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r93_equity_risk_premium_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
