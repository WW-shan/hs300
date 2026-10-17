# 输入说明: R88 — 股权激励/员工持股公告历史可用性审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r88_equity_incentive_probe.py
# 结果 JSON: 同目录 r88_equity_incentive_probe.json。原始缓存 data/research/r88_equity_incentive/ (不提交)。
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r87_sample = _load("r87_sample", "script_r87_holder_concentration_probe.py")
DATA = r87_sample.DATA
CACHE = DATA / "research" / "r88_equity_incentive"
START_DATE = "20200101"
END_DATE = "20261014"
START = date(2020, 1, 1)
END = date(2026, 10, 14)
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), END)
WORKERS = 2
ORG_MAP: dict[str, str] = {}
CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.cninfo.com.cn/",
}
MIN_COUNTS = {
    "equity_plan_launch": {"events": 10, "symbols": 6, "min_discovery_events": 3, "min_validation_events": 2},
    "equity_first_grant": {"events": 3, "symbols": 2, "min_discovery_events": 1, "min_validation_events": 1},
    "esop_launch": {"events": 5, "symbols": 3, "min_discovery_events": 1, "min_validation_events": 1},
}
EXCLUDE_LEGAL = ("法律意见", "独立财务顾问", "核查意见", "审核意见", "摘要")
EXCLUDE_NOISE = ("终止", "取消", "注销", "考核", "名单", "修订", "更新", "说明", "登记", "完成", "解锁", "行权", "归属", "条件成就", "解除限售", "上市流通", "结果", "调整", "更正", "自查")


def _normalize(raw, symbol: str) -> pl.DataFrame:
    if raw is None or len(raw) == 0:
        return pl.DataFrame()
    columns = {
        "代码": "vendor_code",
        "简称": "vendor_name",
        "公告标题": "title",
        "公告时间": "notice_time",
        "公告链接": "url",
    }
    available = [column for column in columns if column in raw.columns]
    if len(available) < 5:
        raise RuntimeError(f"missing columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(columns)
    frame = frame.with_columns([
        pl.lit(symbol).alias("symbol"),
        pl.col("title").cast(pl.Utf8),
        pl.col("notice_time").cast(pl.Utf8),
        pl.col("url").cast(pl.Utf8),
    ]).with_columns(
        pl.col("notice_time").str.slice(0, 10).str.to_date(strict=False).alias("notice_date")
    )
    return (
        frame.filter(
            pl.col("notice_date").is_not_null()
            & (pl.col("notice_date") >= START)
            & (pl.col("notice_date") <= END)
        )
        .unique(subset=["symbol", "notice_date", "title", "url"], keep="last")
        .sort(["notice_date", "title"])
    )


def _load_org_map() -> dict[str, str]:
    import requests

    last_error = "unknown"
    for attempt in range(5):
        try:
            response = requests.get(
                "https://www.cninfo.com.cn/new/data/szse_stock.json",
                headers=CNINFO_HEADERS,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            return {str(item["code"]): str(item["orgId"]) for item in payload["stockList"]}
        except Exception as exc:  # noqa: BLE001 - mapping fetch retries then surfaces
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"CNInfo stock map failed: {last_error}")


def _query_page(code: str, org_id: str, page: int) -> dict:
    import requests

    payload = {
        "pageNum": str(page),
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": f"{code},{org_id}",
        "searchkey": "",
        "secid": "",
        "category": "category_gqjl_szsh",
        "trade": "",
        "seDate": f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:]}~{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:]}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    last_error = "unknown"
    for attempt in range(6):
        try:
            response = requests.post(
                "https://www.cninfo.com.cn/new/hisAnnouncement/query",
                data=payload,
                headers=CNINFO_HEADERS,
                timeout=30,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:80]}")
            return response.json()
        except Exception as exc:  # noqa: BLE001 - page query retries then surfaces
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"page {page}: {last_error}")


def _fetch_symbol(symbol: str) -> dict:
    import math
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    import pandas as pd

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
                    float(milliseconds) / 1000.0,
                    tz=timezone.utc,
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
        frame = _normalize(raw, symbol)
        tmp = path.with_suffix(".tmp")
        frame.write_parquet(tmp)
        tmp.replace(path)
        return {"symbol": symbol, "ok": True, "rows": frame.height, "error": ""}
    except Exception as exc:  # noqa: BLE001 - source audit records failures
        return {
            "symbol": symbol,
            "ok": False,
            "rows": 0,
            "error": f"{type(exc).__name__}: {str(exc)[:180]}",
        }


def _ensure_cache(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    ORG_MAP.update(_load_org_map())
    missing = [symbol for symbol in symbols if not (CACHE / f"{symbol}.parquet").exists()]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_fetch_symbol, symbol): symbol for symbol in missing}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results[result["symbol"]] = result
            if index % 5 == 0 or index == len(futures):
                print(f"equity incentive probe {index}/{len(futures)}", flush=True)
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
    nonempty_symbols = raw["symbol"].n_unique() if raw.height else 0
    manifest = {
        "symbols": len(symbols),
        "success_symbols": len(symbols) - len(failures),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / len(symbols), 6),
        "nonempty_symbols": nonempty_symbols,
        "failures": sorted(failures, key=lambda row: row["symbol"]),
        "rows": raw.height,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw, manifest


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _classify_frame(raw: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for row in raw.iter_rows(named=True):
        title = row["title"] or ""
        if (
            "激励计划" in title
            and "草案" in title
            and not _contains_any(title, EXCLUDE_LEGAL + EXCLUDE_NOISE)
            and not _contains_any(title, ("首次授予", "行权", "归属", "解除限售", "上市流通"))
        ):
            rows.append({**row, "event_type": "equity_plan_launch"})
        if (
            "首次授予" in title
            and _contains_any(title, ("股权激励", "限制性股票", "股票期权"))
            and not _contains_any(title, EXCLUDE_LEGAL + EXCLUDE_NOISE)
            and not _contains_any(title, ("行权", "归属", "解除限售", "上市流通"))
        ):
            rows.append({**row, "event_type": "equity_first_grant"})
        if (
            "员工持股计划" in title
            and "草案" in title
            and not _contains_any(title, EXCLUDE_LEGAL + EXCLUDE_NOISE)
        ):
            rows.append({**row, "event_type": "esop_launch"})
    if not rows:
        return pl.DataFrame(schema={**raw.schema, "event_type": pl.String})
    return pl.DataFrame(rows)


def _event_frame(classified: pl.DataFrame) -> pl.DataFrame:
    if classified.height == 0:
        return pl.DataFrame()
    return (
        classified.group_by(["event_type", "symbol", "notice_date"])
        .agg([
            pl.col("title").sort().first().alias("first_title"),
            pl.len().alias("same_day_docs"),
            pl.col("url").sort().first().alias("first_url"),
        ])
        .sort(["event_type", "notice_date", "symbol"])
    )


def _counts(events: pl.DataFrame) -> dict:
    out = {}
    for event_type in MIN_COUNTS:
        part = events.filter(pl.col("event_type") == event_type) if events.height else pl.DataFrame()
        if part.height == 0:
            out[event_type] = {
                "events": 0, "symbols": 0, "discovery_events": 0, "validation_events": 0,
                "first_date": None, "last_date": None,
            }
            continue
        out[event_type] = {
            "events": part.height,
            "symbols": part["symbol"].n_unique(),
            "discovery_events": part.filter(
                (pl.col("notice_date") >= DISCOVERY[0]) & (pl.col("notice_date") <= DISCOVERY[1])
            ).height,
            "validation_events": part.filter(
                (pl.col("notice_date") >= VALIDATION[0]) & (pl.col("notice_date") <= VALIDATION[1])
            ).height,
            "first_date": str(part["notice_date"].min()),
            "last_date": str(part["notice_date"].max()),
        }
    return out


def main() -> int:
    symbols, labels = r87_sample._sample_symbols()
    raw, manifest = _ensure_cache(symbols)
    classified = _classify_frame(raw)
    events = _event_frame(classified)
    counts = _counts(events)
    readiness = {}
    for event_type, thresholds in MIN_COUNTS.items():
        item = counts[event_type]
        readiness[event_type] = {
            **thresholds,
            "actual_events": item["events"],
            "actual_symbols": item["symbols"],
            "discovery_events": item["discovery_events"],
            "validation_events": item["validation_events"],
            "pass": bool(
                item["events"] >= thresholds["events"]
                and item["symbols"] >= thresholds["symbols"]
                and item["discovery_events"] >= thresholds["min_discovery_events"]
                and item["validation_events"] >= thresholds["min_validation_events"]
            ),
        }
    field_completeness = {
        "rows": raw.height,
        "title_nonnull": int(raw["title"].is_not_null().sum()) if raw.height else 0,
        "notice_date_nonnull": int(raw["notice_date"].is_not_null().sum()) if raw.height else 0,
        "url_nonnull": int(raw["url"].is_not_null().sum()) if raw.height else 0,
    }
    latest_date = raw["notice_date"].max() if raw.height else None
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["title_nonnull"] == raw.height
        and field_completeness["notice_date_nonnull"] == raw.height
        and latest_date is not None
        and latest_date >= date(2026, 8, 31)
    )
    examples = (
        events.group_by("event_type").head(8).sort(["event_type", "notice_date"]).to_dicts()
        if events.height else []
    )
    by_year = (
        events.with_columns(pl.col("notice_date").dt.year().alias("year"))
        .group_by(["event_type", "year"]).len().sort(["event_type", "year"]).to_dicts()
        if events.height else []
    )
    by_stratum = []
    if events.height:
        event_symbols = events["symbol"].unique().to_list()
        for label in sorted(set(labels.values())):
            sample_symbols = {symbol for symbol in symbols if labels[symbol] == label}
            part = events.filter(pl.col("symbol").is_in(sorted(sample_symbols & set(event_symbols))))
            by_stratum.append({
                "stratum": label,
                "sample_symbols": len(sample_symbols),
                "event_symbols": part["symbol"].n_unique(),
                "events": part.height,
            })
    recommended = [
        event_type for event_type, item in readiness.items()
        if item["pass"]
    ]
    result = {
        "purpose": "R88 CNInfo equity-incentive and ESOP announcement history audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])]},
        "sample": {
            "count": len(symbols),
            "symbols": symbols,
            "strata": {
                label: sorted(symbol for symbol in symbols if labels[symbol] == label)
                for label in sorted(set(labels.values()))
            },
            "selection": "same deterministic stratified sample as R87",
        },
        "fetch": manifest,
        "fields": field_completeness,
        "latest_notice_date": str(latest_date) if latest_date else None,
        "raw_announcements": raw.height,
        "classified_documents": classified.height,
        "unique_same_day_events": events.height,
        "counts": counts,
        "readiness": readiness,
        "by_year": by_year,
        "by_stratum": by_stratum,
        "examples": examples,
        "source_ok": source_ok,
        "recommended_categories": recommended,
        "D1_for_recommended_set": bool(recommended and source_ok and all(readiness[event_type]["pass"] for event_type in recommended)),
        "decision": (
            f"proceed to R88 pre-registration for {recommended}"
            if recommended and source_ok
            else "data gap; do not compute R88 event study"
        ),
        "caveat": "Titles are classified from the CNInfo equity-incentive category. Same-day legal opinions, summaries, board opinions and implementation notices are excluded or de-duplicated. No price or return data was inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r88_equity_incentive_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "fetch": manifest,
        "latest": result["latest_notice_date"],
        "raw": raw.height,
        "classified": classified.height,
        "events": events.height,
        "counts": counts,
        "readiness": readiness,
        "recommended": recommended,
        "source_ok": source_ok,
    }, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
