# 输入说明: R80 — 财报首次披露日与更正公告审计。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r80_financial_notice_audit.py
# 原始公告缓存 data/research/r80_financial_notices/ (不入库); 结果 JSON 同目录 r80_financial_notice_audit.json。
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import time
from datetime import date
from pathlib import Path

import polars as pl
import requests

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
DATA = r40.DATA
CACHE_DIR = DATA / "research" / "r80_financial_notices"
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
START = date(2021, 1, 1)
END = date(2026, 10, 14)
NOTICE_BEGIN = "20200101"
NOTICE_END = "20261014"
API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
REPORT_PATTERNS = (
    ("Q1", re.compile(r"(\d{4})\s*年\s*(?:第一季度|一季度)\s*报告")),
    ("H1", re.compile(r"(\d{4})\s*年\s*半年度\s*报告")),
    ("Q3", re.compile(r"(\d{4})\s*年\s*(?:第三季度|三季度)\s*报告")),
    ("FY", re.compile(r"(\d{4})\s*年\s*年度\s*报告|(\d{4})\s*年度\s*报告")),
)
CORRECTION_WORDS = ("更正", "补充", "修订", "修正", "更新后")
PERIOD_ENDS = {
    "Q1": (3, 31),
    "H1": (6, 30),
    "Q3": (9, 30),
    "FY": (12, 31),
}
CACHE_SCHEMA = {
    "symbol": pl.String,
    "title": pl.String,
    "notice_type": pl.String,
    "notice_date": pl.Date,
    "url": pl.String,
}


def _symbols() -> list[str]:
    return sorted(pl.read_parquet(DAILY_MEMBERSHIP)["symbol"].unique().to_list())


def _request_page(session: requests.Session, symbol: str, page: int) -> dict:
    params = {
        "sr": "-1",
        "page_size": "100",
        "page_index": str(page),
        "ann_type": "A",
        "client_source": "web",
        "f_node": "1",
        "s_node": "0",
        "stock_list": symbol[:6],
        "begin_time": NOTICE_BEGIN,
        "end_time": NOTICE_END,
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(API_URL, params=params, timeout=20)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - retry then surface concise failure
            last_error = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"{symbol}: {type(last_error).__name__}: {last_error}")


def _fetch_notices(session: requests.Session, symbol: str) -> pl.DataFrame:
    first = _request_page(session, symbol, 1)
    data = first.get("data") or {}
    total = int(data.get("total_hits") or 0)
    pages = [first]
    for page in range(2, (total + 99) // 100 + 1):
        pages.append(_request_page(session, symbol, page))
    rows = []
    for payload in pages:
        for item in (payload.get("data") or {}).get("list") or []:
            notice_date = str(item.get("notice_date") or "")[:10]
            if not notice_date:
                continue
            columns = item.get("columns") or []
            notice_type = str(columns[0].get("column_name") or "") if columns else ""
            url = f"https://data.eastmoney.com/notices/detail/{symbol[:6]}/{item.get('art_code') or ''}.html"
            rows.append({
                "symbol": symbol,
                "title": str(item.get("title") or item.get("title_ch") or ""),
                "notice_type": notice_type,
                "notice_date": notice_date,
                "url": url,
            })
    if not rows:
        return pl.DataFrame(schema=CACHE_SCHEMA)
    return pl.DataFrame(rows).with_columns(pl.col("notice_date").str.to_date()).unique(
        subset=["symbol", "title", "notice_type", "notice_date"], keep="last"
    ).sort("notice_date")


def _ensure_cache(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/notices/",
    })
    failures = []
    empty = []
    for index, symbol in enumerate(symbols, start=1):
        path = CACHE_DIR / f"{symbol}.parquet"
        if path.exists():
            continue
        try:
            frame = _fetch_notices(session, symbol)
            frame.write_parquet(path)
            if frame.height == 0:
                empty.append(symbol)
        except Exception as exc:  # noqa: BLE001 - record per-symbol failure
            failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        if index % 50 == 0 or index == len(symbols):
            print(f"financial notices {index}/{len(symbols)}", flush=True)

    frames = []
    for symbol in symbols:
        path = CACHE_DIR / f"{symbol}.parquet"
        if path.exists():
            frames.append(pl.read_parquet(path).with_columns([
                pl.col("symbol").cast(pl.String),
                pl.col("title").cast(pl.String),
                pl.col("notice_type").cast(pl.String),
                pl.col("notice_date").cast(pl.Date),
                pl.col("url").cast(pl.String),
            ]))
    all_notices = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame(schema=CACHE_SCHEMA)
    success = len(symbols) - len(failures)
    audit = {
        "symbols": len(symbols),
        "cached_symbols": len(frames),
        "success_symbols": success,
        "success_rate": round(success / len(symbols), 6),
        "empty_symbols": len(empty),
        "empty_symbol_list": empty,
        "failures": failures,
        "rows": all_notices.height,
    }
    return all_notices, audit


def _parse_period(title: str) -> tuple[date, str] | None:
    for label, pattern in REPORT_PATTERNS:
        match = pattern.search(title)
        if not match:
            continue
        year_text = next((value for value in match.groups() if value), None)
        if year_text is None:
            continue
        year = int(year_text)
        month, day = PERIOD_ENDS[label]
        return date(year, month, day), label
    return None


def _period_table(notices: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    rows = []
    unparsed = []
    for row in notices.iter_rows(named=True):
        parsed = _parse_period(row["title"])
        if parsed is None:
            if any(token in row["title"] for token in ("报告", "季度", "半年", "年度")):
                unparsed.append({"title": row["title"], "notice_type": row["notice_type"]})
            continue
        period_end, report_type = parsed
        correction = any(word in row["title"] or word in row["notice_type"] for word in CORRECTION_WORDS)
        rows.append({
            "symbol": row["symbol"],
            "period_end": period_end,
            "report_type": report_type,
            "notice_date": row["notice_date"],
            "correction": correction,
        })
    parsed = pl.DataFrame(rows)
    if parsed.height:
        grouped = parsed.group_by(["symbol", "period_end", "report_type"]).agg([
            pl.col("notice_date").min().alias("first_notice_date"),
            pl.col("notice_date").filter(pl.col("correction")).max().alias("correction_date"),
            pl.col("notice_date").filter(pl.col("correction")).len().alias("correction_notices"),
            pl.len().alias("report_notices"),
        ]).with_columns(
            pl.max_horizontal("first_notice_date", "correction_date").alias("effective_date")
        ).sort(["symbol", "period_end"])
    else:
        grouped = pl.DataFrame()
    unparsed_counts = (
        pl.DataFrame(unparsed)
        .group_by(["title", "notice_type"])
        .len()
        .sort(["len", "title", "notice_type"], descending=[True, False, False])
        if unparsed else pl.DataFrame(schema={"title": pl.String, "notice_type": pl.String, "len": pl.UInt32})
    )
    unparsed_rows = unparsed_counts.to_dicts()
    canonical = json.dumps(unparsed_rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    unparsed_sha = hashlib.sha256(canonical.encode()).hexdigest()
    unparsed_path = CACHE_DIR / "unparsed_reportlike.json"
    unparsed_path.write_text(json.dumps(unparsed_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    audit = {
        "recognized_periods": grouped.height,
        "recognized_notices": parsed.height if parsed.height else 0,
        "corrections": int(parsed.filter(pl.col("correction")).height) if parsed.height else 0,
        "correction_periods": int(grouped.filter(pl.col("correction_date").is_not_null()).height) if grouped.height else 0,
        "unparsed_reportlike": unparsed_counts.height,
        "unparsed_reportlike_rows": int(unparsed_counts["len"].sum()) if unparsed_counts.height else 0,
        "unparsed_reportlike_sha256": unparsed_sha,
        "unparsed_reportlike_cache": "data/research/r80_financial_notices/unparsed_reportlike.json",
        "unparsed_reportlike_top50": unparsed_counts.head(50).to_dicts(),
    }
    return grouped, audit


def _statutory_deadline(period_end: date) -> date:
    from app.backtest.fundamentals import _statutory_deadline as deadline

    return deadline(period_end)


def main() -> int:
    symbols = _symbols()
    notices, api_audit = _ensure_cache(symbols)
    periods, period_audit = _period_table(notices)
    metrics = (
        pl.read_parquet(DATA / "financials" / "metrics" / "part.parquet")
        .select(["symbol", "period_end", "announce_date"])
        .filter(pl.col("symbol").is_in(symbols))
        .with_columns([
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("raw_announce_date"),
        ])
        .unique(subset=["symbol", "period_end"], keep="last")
        .filter(pl.col("period_end") >= date(2020, 3, 31))
    )
    joined = metrics.join(periods, on=["symbol", "period_end"], how="left")
    sample = joined.filter((pl.col("period_end") >= START) & (pl.col("period_end") <= END))
    mapped = sample.filter(pl.col("first_notice_date").is_not_null())
    coverage = mapped.height / sample.height if sample.height else 0.0
    by_type = (
        sample.group_by("report_type")
        .agg([
            pl.len().alias("rows"),
            pl.col("first_notice_date").is_not_null().sum().alias("mapped"),
        ])
        .with_columns((pl.col("mapped") / pl.col("rows")).alias("coverage"))
        .sort("report_type")
    )
    per_period = (
        sample.group_by("period_end")
        .agg([
            pl.len().alias("rows"),
            pl.col("first_notice_date").is_not_null().sum().alias("mapped"),
        ])
        .with_columns((pl.col("mapped") / pl.col("rows")).alias("coverage"))
        .sort("period_end")
    )
    periods_ge_85 = per_period.filter(pl.col("coverage") >= 0.85).height
    periods_ratio = periods_ge_85 / per_period.height if per_period.height else 0.0
    valid_dates = sample.filter(
        pl.col("first_notice_date").is_not_null()
        & (
            (pl.col("first_notice_date") < pl.col("period_end"))
            | (pl.col("first_notice_date") > pl.lit(END, dtype=pl.Date))
            | (
                pl.col("correction_date").is_not_null()
                & (pl.col("correction_date") < pl.col("first_notice_date"))
            )
        )
    )
    lag = sample.with_columns([
        (pl.col("first_notice_date") - pl.col("period_end")).dt.total_days().alias("first_lag_days"),
        (pl.col("raw_announce_date") - pl.col("period_end")).dt.total_days().alias("raw_lag_days"),
    ]).with_columns(
        pl.col("period_end").map_elements(_statutory_deadline, return_dtype=pl.Date).alias("_deadline")
    ).with_columns(
        (pl.col("_deadline") + pl.duration(days=3) - pl.col("period_end")).dt.total_days().alias("fallback_lag_days"),
    ).with_columns(
        (pl.col("fallback_lag_days") - pl.col("first_lag_days")).alias("days_earlier_than_fallback"),
    )
    lag_summary = {}
    for column in ("first_lag_days", "raw_lag_days", "fallback_lag_days", "days_earlier_than_fallback"):
        values = lag[column].drop_nulls()
        lag_summary[column] = {
            "median": float(values.median()) if values.len() else None,
            "p10": float(values.quantile(0.1)) if values.len() else None,
            "p90": float(values.quantile(0.9)) if values.len() else None,
        }

    report_types = set(by_type["report_type"].to_list()) if by_type.height else set()
    d1 = bool(api_audit["success_rate"] >= 0.95 and coverage >= 0.90)
    d2 = bool(report_types >= {"Q1", "H1", "Q3", "FY"} and per_period.height >= 20 and periods_ratio >= 0.90)
    d3 = bool(valid_dates.height == 0)
    decision = {"D1": d1, "D2": d2, "D3": d3, "all_pass": bool(d1 and d2 and d3)}
    result = {
        "window": {"notice_begin": NOTICE_BEGIN, "notice_end": NOTICE_END, "period_start": str(START), "period_end": str(END)},
        "api_audit": api_audit,
        "period_audit": period_audit,
        "local_periods": {
            "rows": sample.height,
            "mapped_rows": mapped.height,
            "coverage": round(coverage, 6),
            "by_type": by_type.to_dicts(),
            "periods": per_period.to_dicts(),
            "periods_ge_85": periods_ge_85,
            "periods_ratio_ge_85": round(periods_ratio, 6),
        },
        "date_audit": {
            "invalid_rows": valid_dates.height,
            "invalid_examples": valid_dates.select([
                "symbol", "period_end", "first_notice_date", "correction_date", "raw_announce_date"
            ]).head(20).to_dicts(),
            "future_notice_rows": lag.filter(pl.col("first_notice_date") > pl.lit(END, dtype=pl.Date)).height,
            "correction_before_first_rows": lag.filter(
                pl.col("correction_date").is_not_null() & (pl.col("correction_date") < pl.col("first_notice_date"))
            ).height,
        },
        "lag_days": lag_summary,
        "effective_date_difference": {
            "correction_shifted_periods": int(periods.filter(pl.col("correction_date").is_not_null()).height) if periods.height else 0,
            "correction_shift_median_days": (
                float(
                    periods.filter(pl.col("correction_date").is_not_null())
                    .with_columns((pl.col("correction_date") - pl.col("first_notice_date")).dt.total_days().alias("shift"))
                    ["shift"].median()
                )
                if periods.height and periods.filter(pl.col("correction_date").is_not_null()).height
                else None
            ),
        },
        "decision": decision,
        "recommendation": "R81 financial factor re-screen with notice dates" if decision["all_pass"] else "data gap; keep statutory fallback",
        "caveats": [
            "公告数据来自东方财富公告大全, 不是交易所原始文件",
            "当前财务数值可能已被重述; 有更正时 effective_date 取最晚更正日以降低前视",
            "无法解析的非定期财务公告不参与映射",
            "公告日期为发布日期, 财报因子仍按严格晚于生效日的交易日使用",
        ],
    }
    path = EV / "r80_financial_notice_audit.json"
    path.write_text(json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("api", api_audit, flush=True)
    print(
        "periods",
        {key: value for key, value in period_audit.items() if key != "unparsed_reportlike_top50"},
        flush=True,
    )
    print("coverage", coverage, "by_type", by_type.to_dicts(), "periods_ratio", periods_ratio, flush=True)
    print("lag", lag_summary, "invalid", valid_dates.height, flush=True)
    print("decision", decision, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
