# 输入说明: R91 — 重大合同中标事件历史可用性审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r91_contract_win_probe.py
# 结果 JSON: 同目录 r91_contract_win_probe.json。原始缓存 data/research/r91_contract_win/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r88 = _load("r88_probe", "script_r88_equity_incentive_probe.py")
DATA = r88.DATA
CACHE = DATA / "research" / "r91_contract_win"
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
START = date(2020, 1, 1)
END = date(2026, 10, 14)
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), END)
WORKERS = 2
ORG_MAP: dict[str, str] = {}
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"}
EXCLUDE = ("终止", "取消", "废标", "无效", "更正", "补充", "进展", "候选人")


def _query_page(code: str, org_id: str, page: int) -> dict:
    import requests

    payload = {
        "pageNum": str(page),
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "中标",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "2020-01-01~2026-10-14",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    last_error = "unknown"
    for attempt in range(5):
        try:
            response = requests.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                data=payload,
                headers={**HEADERS, "Accept-Enckey": _query_page.key},
                timeout=30,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:80]}")
            return response.json()
        except Exception as exc:  # noqa: BLE001 - audit retries
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"page {page}: {last_error}")


def _fetch_symbol(symbol: str) -> dict:
    import pandas as pd
    import requests

    path = CACHE / f"{symbol}.parquet"
    if path.exists() and path.stat().st_size > 0:
        return {"symbol": symbol, "ok": True, "rows": pl.read_parquet(path).height, "error": "cache"}
    code = symbol[:6]
    org_id = ORG_MAP.get(code)
    if not org_id:
        return {"symbol": symbol, "ok": False, "rows": 0, "error": "missing_org_id"}
    try:
        first = _query_page(code, org_id, 1)
        total = int(first.get("totalAnnouncement") or 0)
        pages = max(1, math.ceil(total / 30))
        payloads = [first]
        for page in range(2, pages + 1):
            payloads.append(_query_page(code, org_id, page))
        rows = []
        for payload in payloads:
            for item in payload.get("announcements") or []:
                milliseconds = item.get("announcementTime")
                if milliseconds is None:
                    continue
                notice_time = datetime.fromtimestamp(
                    float(milliseconds) / 1000.0, tz=timezone.utc
                ).astimezone(ZoneInfo("Asia/Shanghai"))
                rows.append({
                    "代码": str(item.get("secCode") or code),
                    "简称": str(item.get("secName") or ""),
                    "公告标题": str(item.get("announcementTitle") or ""),
                    "公告时间": notice_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "公告链接": (
                        "https://www.cninfo.com.cn/new/disclosure/detail"
                        f"?stockCode={item.get('secCode') or code}"
                        f"&announcementId={item.get('announcementId') or ''}"
                        f"&orgId={item.get('orgId') or org_id}"
                        f"&announcementTime={notice_time.strftime('%Y-%m-%d')}"
                    ),
                })
        raw = pd.DataFrame(rows)
        frame = r88._normalize(raw, symbol)
        tmp = path.with_suffix(".tmp")
        frame.write_parquet(tmp)
        tmp.replace(path)
        return {"symbol": symbol, "ok": True, "rows": frame.height, "error": ""}
    except Exception as exc:  # noqa: BLE001 - audit records failures
        return {"symbol": symbol, "ok": False, "rows": 0, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _ensure_cache(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    ORG_MAP.update(r88._load_org_map())
    _query_page.key = r88._enckey() if hasattr(r88, "_enckey") else ""
    if not _query_page.key:
        from py_mini_racer import MiniRacer
        import akshare.stock.stock_hold_control_cninfo as module

        runtime = MiniRacer()
        runtime.eval(module._get_file_content_cninfo("cninfo.js"))
        _query_page.key = runtime.call("getResCode1")
    missing = [symbol for symbol in symbols if not (CACHE / f"{symbol}.parquet").exists()]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_fetch_symbol, symbol): symbol for symbol in missing}
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            results[row["symbol"]] = row
            if index % 25 == 0 or index == len(futures):
                print(f"contract win probe {index}/{len(futures)}", flush=True)
    for symbol in symbols:
        if symbol not in results:
            path = CACHE / f"{symbol}.parquet"
            results[symbol] = {
                "symbol": symbol,
                "ok": path.exists() and path.stat().st_size > 0,
                "rows": pl.read_parquet(path).height if path.exists() and path.stat().st_size > 0 else 0,
                "error": "cache" if path.exists() else "missing",
            }
    frames = [
        pl.read_parquet(CACHE / f"{symbol}.parquet")
        for symbol in symbols
        if (CACHE / f"{symbol}.parquet").exists() and (CACHE / f"{symbol}.parquet").stat().st_size > 0
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    failures = [row for row in results.values() if not row["ok"]]
    manifest = {
        "symbols": len(symbols),
        "success_symbols": len(symbols) - len(failures),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6),
        "nonempty_symbols": raw["symbol"].n_unique() if raw.height else 0,
        "failures": sorted(failures, key=lambda row: row["symbol"]),
        "rows": raw.height,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw, manifest


def _event_frame(raw: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    rows = []
    for row in raw.iter_rows(named=True):
        title = row["title"] or ""
        if "中标" not in title or any(token in title for token in EXCLUDE):
            continue
        rows.append({**row, "event_type": "contract_win"})
    if not rows:
        return pl.DataFrame(), {"classified_documents": 0, "events": 0}
    classified = pl.DataFrame(rows)
    events = (
        classified.group_by(["symbol", "notice_date"])
        .agg([
            pl.col("title").sort().first().alias("first_title"),
            pl.len().alias("same_day_docs"),
        ])
        .sort(["notice_date", "symbol"])
    )
    snapshots = pl.read_parquet(DAILY_MEMBERSHIP).select([
        pl.col("query_date").alias("membership_date"),
        "symbol",
    ]).sort(["symbol", "membership_date"])
    joined = events.sort(["symbol", "notice_date"]).join_asof(
        snapshots,
        left_on="notice_date",
        right_on="membership_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).with_columns(pl.col("membership_date").is_not_null().alias("in_hs300"))
    analysis = joined.filter(
        pl.col("in_hs300")
        & (pl.col("notice_date") >= DISCOVERY[0])
        & (pl.col("notice_date") <= VALIDATION[1])
    )
    audit = {
        "classified_documents": classified.height,
        "events": events.height,
        "membership_matched": int(joined["in_hs300"].sum()),
        "analysis_events": analysis.height,
        "analysis_symbols": analysis["symbol"].n_unique() if analysis.height else 0,
    }
    return analysis, audit


def main() -> int:
    symbols = sorted(pl.read_parquet(DAILY_MEMBERSHIP)["symbol"].unique().to_list())
    raw, manifest = _ensure_cache(symbols)
    events, audit = _event_frame(raw)
    disc = events.filter((pl.col("notice_date") >= DISCOVERY[0]) & (pl.col("notice_date") <= DISCOVERY[1])) if events.height else events
    val = events.filter((pl.col("notice_date") >= VALIDATION[0]) & (pl.col("notice_date") <= VALIDATION[1])) if events.height else events
    symbol_counts = events.group_by("symbol").len().sort("len", descending=True) if events.height else pl.DataFrame()
    month_counts = (
        events.with_columns(pl.col("notice_date").dt.strftime("%Y-%m").alias("month"))
        .group_by("month").len().sort("len", descending=True)
        if events.height else pl.DataFrame()
    )
    counts = {
        "events": events.height,
        "symbols": events["symbol"].n_unique() if events.height else 0,
        "discovery_events": disc.height,
        "discovery_symbols": disc["symbol"].n_unique() if disc.height else 0,
        "validation_events": val.height,
        "validation_symbols": val["symbol"].n_unique() if val.height else 0,
        "max_symbol_share": round(symbol_counts["len"][0] / events.height, 6) if events.height else None,
        "max_symbol": symbol_counts.row(0, named=True) if events.height else None,
        "max_month_share": round(month_counts["len"][0] / events.height, 6) if events.height else None,
        "max_month": month_counts.row(0, named=True) if events.height else None,
        "by_year": (
            events.with_columns(pl.col("notice_date").dt.year().alias("year"))
            .group_by("year").agg([pl.len().alias("events"), pl.col("symbol").n_unique().alias("symbols")])
            .sort("year").to_dicts()
            if events.height else []
        ),
    }
    field_completeness = {
        "rows": raw.height,
        "title_nonnull": int(raw["title"].is_not_null().sum()) if raw.height else 0,
        "notice_date_nonnull": int(raw["notice_date"].is_not_null().sum()) if raw.height else 0,
        "url_nonnull": int(raw["url"].is_not_null().sum()) if raw.height else 0,
    }
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["title_nonnull"] / raw.height >= 0.99
        and field_completeness["notice_date_nonnull"] / raw.height >= 0.99
        and field_completeness["url_nonnull"] / raw.height >= 0.99
    )
    structure_ok = bool(
        counts["events"] >= 100
        and counts["symbols"] >= 30
        and counts["discovery_events"] >= 30
        and counts["validation_events"] >= 30
        and counts["max_symbol_share"] is not None and counts["max_symbol_share"] <= 0.10
        and counts["max_month_share"] is not None and counts["max_month_share"] <= 0.20
    )
    result = {
        "purpose": "R91 major contract-win event history audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])]},
        "keyword": "中标",
        "exclude_tokens": list(EXCLUDE),
        "fetch": manifest,
        "fields": field_completeness,
        "audit": audit,
        "counts": counts,
        "source_ok": source_ok,
        "structure_ok": structure_ok,
        "D1": bool(source_ok and structure_ok),
        "decision": "proceed to R91 pre-registration" if source_ok and structure_ok else "data gap or insufficient breadth; do not compute R91 event study",
        "caveat": "CNInfo full-text keyword search for 中标. Titles containing termination, cancellation, failed bid, correction, supplement, progress or candidate notices are excluded. No returns inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r91_contract_win_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "fetch": manifest,
        "fields": field_completeness,
        "audit": audit,
        "counts": counts,
        "source_ok": source_ok,
        "structure_ok": structure_ok,
    }, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
