# 输入说明: R90 — 董监高真实竞价增持/减持历史可用性审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r90_insider_trade_probe.py
# 结果 JSON: 同目录 r90_insider_trade_probe.json。原始缓存 data/research/r90_insider_trade/ (不提交)。
from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent
DATA = EV.parents[5] / "data"
CACHE = DATA / "research" / "r90_insider_trade"
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
START = date(2020, 1, 1)
END = date(2026, 10, 14)
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), END)
OPEN_MARKET_METHODS = {"竞价交易", "二级市场买卖"}
DIRECTIONS = {"B": "buy", "S": "sell"}


def _periods() -> list[tuple[date, date]]:
    out = []
    for year in range(2020, 2027):
        for start_month, end_month, end_day in ((1, 6, 30), (7, 12, 31)):
            start = date(year, start_month, 1)
            end = date(year, end_month, end_day)
            if end < START or start > END:
                continue
            out.append((max(start, START), min(end, END)))
    return out


def _market_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    return f"{code}.BJ"


def _enckey() -> str:
    from py_mini_racer import MiniRacer
    import akshare.stock.stock_hold_control_cninfo as module

    runtime = MiniRacer()
    runtime.eval(module._get_file_content_cninfo("cninfo.js"))
    return runtime.call("getResCode1")


def _fetch_period(start: date, end: date, direction: str, key: str) -> pl.DataFrame:
    import requests

    url = "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1030"
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Cache-Control": "no-cache",
        "Content-Length": "0",
        "Host": "webapi.cninfo.com.cn",
        "Accept-Enckey": key,
        "Origin": "https://webapi.cninfo.com.cn",
        "Pragma": "no-cache",
        "Referer": "https://webapi.cninfo.com.cn/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }
    params = {
        "sdate": start.strftime("%Y%m%d"),
        "edate": end.strftime("%Y%m%d"),
        "varytype": direction,
    }
    last_error = "unknown"
    payload = None
    for attempt in range(5):
        try:
            response = requests.post(url, headers=headers, params=params, timeout=90)
            response.raise_for_status()
            payload = response.json()
            if payload.get("resultcode") != 200:
                raise RuntimeError(str(payload)[:180])
            break
        except Exception as exc:  # noqa: BLE001 - audit retries and records failures
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < 4:
                time.sleep(2.0 * (attempt + 1))
    if payload is None:
        raise RuntimeError(last_error)
    records = payload.get("records") or []
    if len(records) >= 20000:
        raise RuntimeError(f"period hit 20000 cap: {start}~{end} {direction}")
    rows = []
    for item in records:
        method = str(item.get("F010V") or "")
        if method not in OPEN_MARKET_METHODS:
            continue
        announce_date = str(item.get("DECLAREDATE") or "")[:10]
        transaction_date = str(item.get("ENDDATE") or "")[:10]
        code = str(item.get("SECCODE") or "").zfill(6)
        rows.append({
            "symbol": _market_symbol(code),
            "announce_date": announce_date,
            "transaction_date": transaction_date,
            "executive_name": str(item.get("HUMANNAME") or ""),
            "position": str(item.get("F002V") or ""),
            "relation": str(item.get("F003V") or ""),
            "change_qty": item.get("F006N"),
            "avg_price": item.get("F008N"),
            "market_value": item.get("F009N"),
            "method": method,
            "direction": DIRECTIONS[direction],
        })
    if not rows:
        return pl.DataFrame(schema={
            "symbol": pl.String, "announce_date": pl.Date, "transaction_date": pl.Date,
            "executive_name": pl.String, "position": pl.String, "relation": pl.String,
            "change_qty": pl.Float64, "avg_price": pl.Float64, "market_value": pl.Float64,
            "method": pl.String, "direction": pl.String,
        })
    return (
        pl.DataFrame(rows)
        .with_columns([
            pl.col("announce_date").str.to_date(strict=False),
            pl.col("transaction_date").str.to_date(strict=False),
            pl.col("change_qty").cast(pl.Float64, strict=False),
            pl.col("avg_price").cast(pl.Float64, strict=False),
            pl.col("market_value").cast(pl.Float64, strict=False),
        ])
        .filter(pl.col("announce_date").is_not_null())
        .unique(subset=["symbol", "announce_date", "executive_name", "change_qty", "avg_price", "direction"], keep="last")
    )


def _ensure_cache() -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = _enckey()
    failures = []
    periods = _periods()
    for start, end in periods:
        label = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
        for code, direction in DIRECTIONS.items():
            path = CACHE / f"{direction}_{label}.parquet"
            if path.exists() and path.stat().st_size > 0:
                continue
            try:
                frame = _fetch_period(start, end, code, key)
                frame.write_parquet(path)
            except Exception as exc:  # noqa: BLE001 - audit records failures
                failures.append({"period": label, "direction": direction, "error": f"{type(exc).__name__}: {str(exc)[:180]}"})
    frames = [
        pl.read_parquet(path)
        for path in sorted(CACHE.glob("*.parquet"))
        if path.name.startswith(("buy_", "sell_")) and path.stat().st_size > 0
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    expected = len(periods) * 2
    manifest = {
        "periods": len(periods),
        "requests": expected,
        "successful_files": expected - len(failures),
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / expected, 6),
        "failures": failures,
        "rows": raw.height,
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return raw, manifest


def _membership_events(raw: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    snapshots = pl.read_parquet(DAILY_MEMBERSHIP).select([
        pl.col("query_date").alias("membership_date"),
        "symbol",
    ]).sort(["symbol", "membership_date"])
    joined = raw.sort(["symbol", "announce_date"]).join_asof(
        snapshots,
        left_on="announce_date",
        right_on="membership_date",
        by="symbol",
        strategy="backward",
        check_sortedness=False,
    ).with_columns(pl.col("membership_date").is_not_null().alias("in_hs300"))
    analysis = joined.filter(
        pl.col("in_hs300")
        & (pl.col("announce_date") >= DISCOVERY[0])
        & (pl.col("announce_date") <= VALIDATION[1])
    )
    events = (
        analysis.group_by(["direction", "symbol", "announce_date"])
        .agg([
            pl.len().alias("records"),
            pl.col("executive_name").n_unique().alias("executives"),
            pl.col("position").sort().first().alias("position"),
            pl.col("change_qty").sum().alias("change_qty"),
            (pl.col("change_qty").abs() * pl.col("avg_price")).sum().alias("estimated_amount"),
            pl.col("avg_price").is_not_null().sum().alias("price_nonnull"),
        ])
        .sort(["direction", "announce_date", "symbol"])
    )
    audit = {
        "raw_rows": raw.height,
        "raw_symbols": raw["symbol"].n_unique() if raw.height else 0,
        "membership_matched": int(joined["in_hs300"].sum()),
        "analysis_records": analysis.height,
        "events": events.height,
        "event_symbols": events["symbol"].n_unique() if events.height else 0,
    }
    return events, audit


def main() -> int:
    raw, manifest = _ensure_cache()
    events, membership = _membership_events(raw)
    counts = {}
    for direction in ("buy", "sell"):
        part = events.filter(pl.col("direction") == direction)
        disc = part.filter((pl.col("announce_date") >= DISCOVERY[0]) & (pl.col("announce_date") <= DISCOVERY[1]))
        val = part.filter((pl.col("announce_date") >= VALIDATION[0]) & (pl.col("announce_date") <= VALIDATION[1]))
        by_year = (
            part.with_columns(pl.col("announce_date").dt.year().alias("year"))
            .group_by("year").agg([
                pl.len().alias("events"),
                pl.col("symbol").n_unique().alias("symbols"),
                pl.col("executives").sum().alias("executives"),
            ]).sort("year").to_dicts()
        )
        counts[direction] = {
            "events": part.height,
            "symbols": part["symbol"].n_unique() if part.height else 0,
            "discovery_events": disc.height,
            "discovery_symbols": disc["symbol"].n_unique() if disc.height else 0,
            "validation_events": val.height,
            "validation_symbols": val["symbol"].n_unique() if val.height else 0,
            "by_year": by_year,
            "same_day_multi_executive": part.filter(pl.col("executives") > 1).height,
        }
    field_completeness = {
        "rows": raw.height,
        "avg_price_nonnull": int(raw["avg_price"].is_not_null().sum()) if raw.height else 0,
        "change_qty_nonnull": int(raw["change_qty"].is_not_null().sum()) if raw.height else 0,
        "transaction_date_nonnull": int(raw["transaction_date"].is_not_null().sum()) if raw.height else 0,
    }
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["avg_price_nonnull"] / raw.height >= 0.90
        and field_completeness["change_qty_nonnull"] / raw.height >= 0.90
    )
    readiness = {}
    for direction, threshold in (("buy", {"events": 100, "symbols": 50, "disc": 30, "val": 30}), ("sell", {"events": 100, "symbols": 50, "disc": 30, "val": 30})):
        item = counts[direction]
        readiness[direction] = {
            **threshold,
            "actual_events": item["events"],
            "actual_symbols": item["symbols"],
            "actual_discovery": item["discovery_events"],
            "actual_validation": item["validation_events"],
            "pass": bool(
                item["events"] >= threshold["events"]
                and item["symbols"] >= threshold["symbols"]
                and item["discovery_events"] >= threshold["disc"]
                and item["validation_events"] >= threshold["val"]
            ),
        }
    result = {
        "purpose": "R90 insider open-market buy/sell event history audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])], "validation": [str(VALIDATION[0]), str(VALIDATION[1])]},
        "method_filter": sorted(OPEN_MARKET_METHODS),
        "fetch": manifest,
        "fields": field_completeness,
        "membership": membership,
        "counts": counts,
        "readiness": readiness,
        "source_ok": source_ok,
        "D1_for_events": bool(source_ok and all(item["pass"] for item in readiness.values())),
        "decision": "proceed to R90 pre-registration" if source_ok and all(item["pass"] for item in readiness.values()) else "data gap; do not compute R90 event study",
        "caveat": "Only temporary announcements with open-market methods (竞价交易/二级市场买卖) are included; dividends, equity incentives, block trades, other methods and periodic-report rows are excluded. Announcement date is used for PIT.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r90_insider_trade_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "fetch": manifest,
        "fields": field_completeness,
        "membership": membership,
        "counts": counts,
        "readiness": readiness,
        "source_ok": source_ok,
    }, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
