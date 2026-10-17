# 输入说明: R89 — 分红增长/首次分红历史与逐月覆盖审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r89_dividend_growth_probe.py
# 结果 JSON: 同目录 r89_dividend_growth_probe.json。原始缓存 data/research/r89_dividend_growth/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import StringIO
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
DAILY_MEMBERSHIP = r87_sample.DAILY_MEMBERSHIP
CACHE = DATA / "research" / "r89_dividend_growth"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
WORKERS = 4
MAX_AGE_DAYS = 365


def _download(symbol: str):
    import pandas as pd
    import requests

    url = (
        "https://vip.stock.finance.sina.com.cn/corp/go.php/"
        f"vISSUE_ShareBonus/stockid/{symbol[:6]}.phtml"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))
    if len(tables) <= 12:
        raise RuntimeError(f"dividend table missing: tables={len(tables)}")
    raw = tables[12].copy()
    if raw.empty:
        return pd.DataFrame()
    first = str(raw.iloc[0, 0])
    if "暂时没有数据" in first:
        return pd.DataFrame()
    try:
        raw.columns = [item[2] for item in raw.columns.tolist()]
    except Exception:
        raw.columns = [str(item[0]) for item in raw.columns.tolist()]
    expected = ["公告日期", "送股", "转增", "派息", "进度", "除权除息日", "股权登记日", "红股上市日"]
    renamed = {}
    for column in raw.columns:
        text = str(column)
        for target in expected:
            if text == target or text.startswith(target):
                renamed[column] = target
                break
    raw = raw.rename(columns=renamed)
    if not all(column in raw.columns for column in expected):
        raise RuntimeError(f"dividend columns missing: {list(raw.columns)}")
    return raw[expected].copy()


def _normalize(raw, symbol: str) -> pl.DataFrame:
    if raw is None or len(raw) == 0:
        return pl.DataFrame()
    frame = pl.from_pandas(raw.copy()).with_columns([
        pl.lit(symbol).alias("symbol"),
        pl.col("公告日期").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False).alias("announce_date"),
        pl.col("送股").cast(pl.Float64, strict=False).fill_null(0.0).alias("stock_dividend_per10"),
        pl.col("转增").cast(pl.Float64, strict=False).fill_null(0.0).alias("transfer_per10"),
        pl.col("派息").cast(pl.Float64, strict=False).fill_null(0.0).alias("cash_per10"),
        pl.col("进度").cast(pl.Utf8).alias("progress"),
    ])
    frame = frame.filter(pl.col("announce_date").is_not_null()).with_columns(
        ((pl.col("stock_dividend_per10") + pl.col("transfer_per10")) / 10.0 + 1.0).alias("split_multiplier")
    )
    grouped = frame.group_by(["symbol", "announce_date"]).agg([
        pl.col("cash_per10").sum().alias("cash_per10"),
        pl.col("stock_dividend_per10").sum().alias("stock_dividend_per10"),
        pl.col("transfer_per10").sum().alias("transfer_per10"),
        pl.col("split_multiplier").product().alias("split_multiplier"),
        pl.col("progress").sort().first().alias("progress"),
    ])
    return grouped.filter(pl.col("progress") == "实施").sort(["symbol", "announce_date"])


def _fetch_symbol(symbol: str) -> dict:
    path = CACHE / f"{symbol}.parquet"
    if path.exists() and path.stat().st_size > 0:
        return {"symbol": symbol, "ok": True, "rows": pl.read_parquet(path).height, "error": "cache"}
    last_error = "unknown"
    for attempt in range(4):
        try:
            frame = _normalize(_download(symbol), symbol)
            tmp = path.with_suffix(".tmp")
            frame.write_parquet(tmp)
            tmp.replace(path)
            return {"symbol": symbol, "ok": True, "rows": frame.height, "error": ""}
        except Exception as exc:  # noqa: BLE001 - source audit records failures
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < 3:
                time.sleep(1.0 * (attempt + 1))
    return {"symbol": symbol, "ok": False, "rows": 0, "error": last_error}


def _ensure_cache(symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    missing = [symbol for symbol in symbols if not (CACHE / f"{symbol}.parquet").exists()]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_fetch_symbol, symbol): symbol for symbol in missing}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results[result["symbol"]] = result
            if index % 5 == 0 or index == len(futures):
                print(f"dividend growth probe {index}/{len(futures)}", flush=True)
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


def _ttm_factor(events: pl.DataFrame, day: date) -> dict:
    available = events.filter(pl.col("announce_date") < day)
    window = available.filter(
        (pl.col("announce_date") > pl.lit(day).dt.offset_by("-730d"))
        & (pl.col("announce_date") <= pl.lit(day).dt.offset_by("-1d"))
    )
    if window.height == 0:
        return {"dividend_ttm_cash": 0.0, "dividend_growth_ttm": None, "dividend_initiation_flag": 0.0}
    rows = window.to_dicts()
    current = 0.0
    previous = 0.0
    for row in rows:
        event_date = row["announce_date"]
        later_splits = [
            item["split_multiplier"]
            for item in rows
            if item["announce_date"] >= event_date and item["announce_date"] <= day
        ]
        normalizer = math.prod(later_splits) if later_splits else 1.0
        cash = float(row["cash_per10"]) / 10.0 / normalizer
        if event_date > date.fromordinal(day.toordinal() - MAX_AGE_DAYS):
            current += cash
        else:
            previous += cash
    growth = current / previous - 1.0 if previous > 0 else None
    initiation = 1.0 if current > 0 and previous <= 0 else 0.0
    return {
        "dividend_ttm_cash": round(current, 10),
        "dividend_growth_ttm": round(growth, 10) if growth is not None else None,
        "dividend_initiation_flag": initiation,
    }


def _month_end_members(symbols: list[str]) -> pl.DataFrame:
    snapshots = pl.read_parquet(DAILY_MEMBERSHIP).filter(
        (pl.col("query_date") >= START) & (pl.col("query_date") <= END)
    )
    month_last = (
        snapshots.with_columns(pl.col("query_date").dt.strftime("%Y-%m").alias("_month"))
        .group_by("_month").agg(pl.col("query_date").max().alias("query_date"))
        .sort("query_date")
    )
    return (
        snapshots.join(month_last, on="query_date")
        .filter(pl.col("symbol").is_in(symbols))
        .select([pl.col("query_date").alias("date"), "symbol"])
        .sort(["date", "symbol"])
    )


def _sample_monthly_factors(raw: pl.DataFrame, symbols: list[str]) -> tuple[pl.DataFrame, dict]:
    members = _month_end_members(symbols)
    rows = []
    events_by_symbol = {
        keys[0] if isinstance(keys, tuple) else keys: group
        for keys, group in raw.group_by("symbol", maintain_order=True)
    }
    for group in members.partition_by("date", maintain_order=True):
        day = group["date"][0]
        for symbol in group["symbol"].to_list():
            events = events_by_symbol.get(symbol)
            if events is None:
                factors = {"dividend_ttm_cash": 0.0, "dividend_growth_ttm": None, "dividend_initiation_flag": 0.0}
            else:
                factors = _ttm_factor(events, day)
            rows.append({"date": day, "symbol": symbol, **factors})
    panel = pl.DataFrame(rows)
    coverage = {}
    for col in ("dividend_growth_ttm", "dividend_initiation_flag"):
        grouped = panel.group_by("date").agg([
            pl.len().alias("eligible"),
            pl.col(col).is_not_null().sum().alias("valid"),
            (pl.col(col) > 0).sum().alias("positive"),
        ])
        coverage[col] = {
            "mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6),
            "min_valid": int(grouped["valid"].min()),
            "months": grouped.height,
            "months_ge70pct": int(((grouped["valid"] / grouped["eligible"]) >= 0.70).sum()),
            "positive_rows": int((panel[col] > 0).sum()) if col == "dividend_initiation_flag" else None,
        }
    return panel, coverage


def main() -> int:
    symbols, labels = r87_sample._sample_symbols()
    raw, manifest = _ensure_cache(symbols)
    monthly, coverage = _sample_monthly_factors(raw, symbols)
    cash_events = raw.filter((pl.col("cash_per10") > 0) & (pl.col("announce_date") >= date(2020, 1, 1)))
    by_year = (
        cash_events.with_columns(pl.col("announce_date").dt.year().alias("year"))
        .group_by("year").agg([
            pl.len().alias("cash_events"),
            pl.col("symbol").n_unique().alias("symbols"),
        ]).sort("year")
    )
    latest = raw["announce_date"].max() if raw.height else None
    field_completeness = {
        "rows": raw.height,
        "announce_date_nonnull": int(raw["announce_date"].is_not_null().sum()) if raw.height else 0,
        "cash_per10_nonnull": int(raw["cash_per10"].is_not_null().sum()) if raw.height else 0,
        "split_multiplier_positive": int((raw["split_multiplier"] > 0).sum()) if raw.height else 0,
    }
    source_ok = bool(
        manifest["failure_rate"] <= 0.10
        and raw.height > 0
        and field_completeness["announce_date_nonnull"] == raw.height
        and field_completeness["cash_per10_nonnull"] == raw.height
        and latest is not None
        and latest >= date(2026, 8, 31)
    )
    factor_ok = bool(
        coverage["dividend_growth_ttm"]["mean"] >= 0.70
        and coverage["dividend_growth_ttm"]["months_ge70pct"] >= 50
        and coverage["dividend_initiation_flag"]["positive_rows"] >= 3
    )
    result = {
        "purpose": "R89 dividend growth/initiation history and monthly coverage audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "ttm_days": MAX_AGE_DAYS},
        "sample": {
            "count": len(symbols),
            "symbols": symbols,
            "strata": {
                label: sorted(symbol for symbol in symbols if labels[symbol] == label)
                for label in sorted(set(labels.values()))
            },
            "selection": "same deterministic stratified sample as R87/R88",
        },
        "fetch": manifest,
        "fields": field_completeness,
        "latest_announce_date": str(latest) if latest else None,
        "cash_events_by_year": by_year.to_dicts(),
        "monthly_coverage": coverage,
        "monthly_rows": monthly.height,
        "source_ok": source_ok,
        "factor_ok": factor_ok,
        "D1": bool(source_ok and factor_ok),
        "decision": "proceed to R89 pre-registration and full-history factor screen" if source_ok and factor_ok else "data gap; do not compute R89 factors",
        "caveat": "Cash per share is normalized for subsequent stock dividends/transfers when constructing TTM growth. No price or return data was inspected.",
    }
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = EV / "r89_dividend_growth_probe.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "fetch": manifest,
        "latest": result["latest_announce_date"],
        "fields": field_completeness,
        "by_year": by_year.to_dicts(),
        "coverage": coverage,
        "monthly_rows": monthly.height,
        "D1": result["D1"],
    }, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
