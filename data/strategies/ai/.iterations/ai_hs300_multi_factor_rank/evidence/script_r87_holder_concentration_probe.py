# 输入说明: R87 — 前十大流通股东集中度历史截面可用性审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r87_holder_concentration_probe.py
# 结果 JSON: 同目录 r87_holder_concentration_probe.json。原始样本缓存 data/research/r87_holder_concentration/ (不提交)。
from __future__ import annotations

import hashlib
import importlib.util
import json
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


r80 = _load("r80", "script_r80_financial_notice_audit.py")
DATA = r80.DATA
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
NOTICE_CACHE = DATA / "research" / "r80_financial_notices"
CACHE = DATA / "research" / "r87_holder_concentration"
SEED = "r87-holder-concentration-20261017"
START_QUARTER = date(2021, 3, 31)
END_QUARTER = date(2026, 6, 30)
EARLY_DATE = date(2021, 6, 1)
LATE_DATE = date(2026, 10, 14)
SAMPLE_PER_STRATUM = 8
WORKERS = 4
API_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageSDLTGD"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://emweb.securities.eastmoney.com/",
}


def _quarter_ends() -> list[date]:
    periods: list[date] = []
    for year in range(START_QUARTER.year, END_QUARTER.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            value = date(year, month, day)
            if START_QUARTER <= value <= END_QUARTER:
                periods.append(value)
    return periods


def _snapshot_symbols(day: date) -> set[str]:
    snapshots = pl.read_parquet(DAILY_MEMBERSHIP)
    available = snapshots.filter(pl.col("query_date") <= day)["query_date"].max()
    if available is None:
        raise RuntimeError(f"missing membership snapshot on or before {day}")
    return set(snapshots.filter(pl.col("query_date") == available)["symbol"].to_list())


def _sample_symbols() -> tuple[list[str], dict[str, str]]:
    early = _snapshot_symbols(EARLY_DATE)
    late = _snapshot_symbols(LATE_DATE)
    strata = {
        "persistent": sorted(early & late),
        "exited": sorted(early - late),
        "entered": sorted(late - early),
    }

    def rank(symbol: str) -> str:
        return hashlib.sha256(f"{SEED}:{symbol}".encode()).hexdigest()

    selected: list[str] = []
    labels: dict[str, str] = {}
    for label, symbols in strata.items():
        chosen = sorted(symbols, key=rank)[:SAMPLE_PER_STRATUM]
        if len(chosen) < SAMPLE_PER_STRATUM:
            raise RuntimeError(f"insufficient {label} sample: {len(chosen)}")
        selected.extend(chosen)
        labels.update({symbol: label for symbol in chosen})
    return selected, labels


def _market_symbol(symbol: str) -> str:
    code, exchange = symbol.split(".")
    return f"{exchange.lower()}{code}"


def _fetch_period(symbol: str, period: date) -> dict:
    import requests

    params = {"code": _market_symbol(symbol), "date": period.isoformat()}
    last_error = "unknown"
    for attempt in range(3):
        try:
            response = requests.get(API_URL, params=params, headers=HEADERS, timeout=25)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("sdltgd")
            if not isinstance(rows, list) or not rows:
                return {
                    "symbol": symbol,
                    "period_end": period,
                    "request_ok": False,
                    "end_date_match": False,
                    "rows": 0,
                    "ratio_nonnull": 0,
                    "ratio_sum": None,
                    "ratio_hhi": None,
                    "ratio_top1": None,
                    "max_rank": None,
                    "error": f"empty_or_api_error:{str(payload)[:120]}",
                }
            parsed = []
            matched = 0
            for row in rows:
                try:
                    rank = int(row.get("HOLDER_RANK"))
                except (TypeError, ValueError):
                    continue
                end_date = str(row.get("END_DATE") or "")[:10]
                if end_date == period.isoformat():
                    matched += 1
                ratio = row.get("FREE_HOLDNUM_RATIO")
                try:
                    ratio = float(ratio) if ratio is not None else None
                except (TypeError, ValueError):
                    ratio = None
                parsed.append((rank, ratio))
            ratios = [ratio for _, ratio in parsed if ratio is not None]
            fractions = [ratio / 100.0 for ratio in ratios]
            return {
                "symbol": symbol,
                "period_end": period,
                "request_ok": True,
                "end_date_match": matched == len(rows) and matched > 0,
                "rows": len(rows),
                "ratio_nonnull": len(ratios),
                "ratio_sum": round(sum(ratios), 8),
                "ratio_hhi": round(sum(value * value for value in fractions), 10),
                "ratio_top1": round(ratios[0], 8) if ratios else None,
                "max_rank": max((rank for rank, _ in parsed), default=None),
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001 - availability audit records provider failures
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return {
        "symbol": symbol,
        "period_end": period,
        "request_ok": False,
        "end_date_match": False,
        "rows": 0,
        "ratio_nonnull": 0,
        "ratio_sum": None,
        "ratio_hhi": None,
        "ratio_top1": None,
        "max_rank": None,
        "error": last_error,
    }


def _load_notices() -> pl.DataFrame:
    frames = [
        pl.read_parquet(path)
        for path in sorted(NOTICE_CACHE.glob("*.parquet"))
        if path.stat().st_size > 0
    ]
    if not frames:
        raise RuntimeError("R80 notice cache is empty")
    return pl.concat(frames, how="diagonal_relaxed")


def _notice_dates() -> pl.DataFrame:
    _, _ = r80._period_table(_load_notices())
    notices = _load_notices()
    periods, _ = r80._period_table(notices)
    return periods.select(["symbol", "period_end", "effective_date", "first_notice_date"])


def _fetch_all(symbols: list[str], periods: list[date]) -> pl.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    missing: list[tuple[str, date]] = []
    cached: list[pl.DataFrame] = []
    expected = {period.isoformat() for period in periods}
    for symbol in symbols:
        path = CACHE / f"{symbol}.parquet"
        if path.exists() and path.stat().st_size > 0:
            frame = pl.read_parquet(path)
            observed = {str(value)[:10] for value in frame["period_end"].to_list()}
            cached.append(frame)
        else:
            frame = pl.DataFrame()
            observed = set()
        missing.extend((symbol, period) for period in periods if period.isoformat() not in observed)
        if observed - expected:
            raise RuntimeError(f"unexpected cached periods for {symbol}")
    if missing:
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(_fetch_period, symbol, period): (symbol, period)
                for symbol, period in missing
            }
            for index, future in enumerate(as_completed(futures), start=1):
                rows.append(future.result())
                if index % 50 == 0 or index == len(futures):
                    print(f"holder concentration probe {index}/{len(futures)}", flush=True)
        fresh = pl.DataFrame(rows).with_columns(pl.col("period_end").cast(pl.Date))
        for symbol in symbols:
            path = CACHE / f"{symbol}.parquet"
            old = pl.read_parquet(path) if path.exists() and path.stat().st_size > 0 else None
            part = fresh.filter(pl.col("symbol") == symbol)
            combined = pl.concat([frame, part], how="diagonal_relaxed") if (frame := old) is not None else part
            if combined.height:
                combined.unique(subset=["symbol", "period_end"], keep="last").sort("period_end").write_parquet(path)
    frames = [
        pl.read_parquet(CACHE / f"{symbol}.parquet")
        for symbol in symbols
        if (CACHE / f"{symbol}.parquet").exists()
    ]
    return pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "period_end"])


def _quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(p * (len(ordered) - 1)))])


def main() -> int:
    symbols, labels = _sample_symbols()
    periods = _quarter_ends()
    raw = _fetch_all(symbols, periods)
    notices = _notice_dates()
    joined = raw.join(notices, on=["symbol", "period_end"], how="left").with_columns([
        (pl.col("effective_date") - pl.col("period_end")).dt.total_days().alias("disclosure_lag_days"),
        pl.col("symbol").replace_strict(labels, return_dtype=pl.String).alias("stratum"),
    ])

    requested = len(symbols) * len(periods)
    request_ok = int(joined["request_ok"].sum())
    exact_dates = int(joined["end_date_match"].sum())
    rows_10 = int((joined["rows"] == 10).sum())
    ratios_10 = int((joined["ratio_nonnull"] == 10).sum())
    plausible = int(
        (
            (joined["ratio_nonnull"] == 10)
            & (joined["ratio_sum"] >= 0)
            & (joined["ratio_sum"] <= 100.01)
        ).sum()
    )
    mapped = int(joined["effective_date"].is_not_null().sum())
    mapped_after_period = int((joined["disclosure_lag_days"] >= 0).sum())
    usable = int(
        (
            joined["request_ok"]
            & joined["end_date_match"]
            & (joined["ratio_nonnull"] == 10)
            & (joined["ratio_sum"] >= 0)
            & (joined["ratio_sum"] <= 100.01)
            & joined["effective_date"].is_not_null()
            & (joined["disclosure_lag_days"] >= 0)
        ).sum()
    )
    sums = joined.filter(
        pl.col("ratio_nonnull") == 10
    )["ratio_sum"].drop_nulls().to_list()
    by_stratum = []
    for label in sorted(set(labels.values())):
        part = joined.filter(pl.col("stratum") == label)
        by_stratum.append({
            "stratum": label,
            "symbols": int(part["symbol"].n_unique()),
            "requested": part.height,
            "usable": int(
                (
                    part["request_ok"]
                    & part["end_date_match"]
                    & (part["ratio_nonnull"] == 10)
                    & (part["ratio_sum"] >= 0)
                    & (part["ratio_sum"] <= 100.01)
                    & part["effective_date"].is_not_null()
                    & (part["disclosure_lag_days"] >= 0)
                ).sum()
            ),
        })
    failures = (
        joined.filter(~pl.col("request_ok"))
        .select(["symbol", "period_end", "error"])
        .with_columns(pl.col("period_end").cast(pl.String))
        .to_dicts()
    )
    mismatch = (
        joined.filter(pl.col("request_ok") & ~pl.col("end_date_match"))
        .select(["symbol", "period_end", "rows"])
        .with_columns(pl.col("period_end").cast(pl.String))
        .to_dicts()
    )
    thresholds = {
        "request_success_rate_min": 0.90,
        "end_date_match_rate_min": 0.95,
        "ten_ratio_rows_rate_min": 0.95,
        "disclosure_mapping_rate_min": 0.90,
        "usable_rate_min": 0.90,
    }
    rates = {
        "request_success_rate": round(request_ok / requested, 6),
        "end_date_match_rate": round(exact_dates / requested, 6),
        "ten_ratio_rows_rate": round(ratios_10 / requested, 6),
        "disclosure_mapping_rate": round(mapped / requested, 6),
        "usable_rate": round(usable / requested, 6),
    }
    d1 = bool(
        rates["request_success_rate"] >= thresholds["request_success_rate_min"]
        and rates["end_date_match_rate"] >= thresholds["end_date_match_rate_min"]
        and rates["ten_ratio_rows_rate"] >= thresholds["ten_ratio_rows_rate_min"]
        and rates["disclosure_mapping_rate"] >= thresholds["disclosure_mapping_rate_min"]
        and rates["usable_rate"] >= thresholds["usable_rate_min"]
    )
    result = {
        "purpose": "R87 top-10 free-float holder concentration availability audit; no returns inspected",
        "window": {
            "period_start": str(periods[0]),
            "period_end": str(periods[-1]),
            "quarter_count": len(periods),
            "early_membership_date": str(EARLY_DATE),
            "late_membership_date": str(LATE_DATE),
        },
        "sample": {
            "symbols": symbols,
            "count": len(symbols),
            "strata": {
                label: sorted(symbol for symbol in symbols if labels[symbol] == label)
                for label in sorted(set(labels.values()))
            },
            "selection": f"SHA-256 rank within membership strata, seed={SEED}",
        },
        "counts": {
            "requested": requested,
            "request_ok": request_ok,
            "end_date_match": exact_dates,
            "rows_10": rows_10,
            "ratio_nonnull_10": ratios_10,
            "ratio_sum_plausible": plausible,
            "notice_mapped": mapped,
            "notice_after_period": mapped_after_period,
            "usable": usable,
        },
        "rates": rates,
        "thresholds": thresholds,
        "ratio_sum_distribution": {
            "n": len(sums),
            "min": round(min(sums), 8) if sums else None,
            "p10": round(_quantile(sums, 0.10), 8) if sums else None,
            "median": round(_quantile(sums, 0.50), 8) if sums else None,
            "p90": round(_quantile(sums, 0.90), 8) if sums else None,
            "max": round(max(sums), 8) if sums else None,
        },
        "disclosure_lag_days": {
            "n": int(joined["disclosure_lag_days"].is_not_null().sum()),
            "min": int(joined["disclosure_lag_days"].min()) if joined["disclosure_lag_days"].is_not_null().any() else None,
            "median": float(joined["disclosure_lag_days"].median()) if joined["disclosure_lag_days"].is_not_null().any() else None,
            "p90": float(joined["disclosure_lag_days"].quantile(0.90)) if joined["disclosure_lag_days"].is_not_null().any() else None,
            "max": int(joined["disclosure_lag_days"].max()) if joined["disclosure_lag_days"].is_not_null().any() else None,
        },
        "by_stratum": by_stratum,
        "failures": failures,
        "end_date_mismatches": mismatch,
        "D1": d1,
        "decision": "proceed to R87 pre-registration and full-history factor screen" if d1 else "data gap; do not compute R87 factors",
        "caveat": "Top-10 free-float holdings are mapped to the first periodic-report disclosure date; corrections use the latest parsed correction date. No current snapshot is backfilled.",
    }
    path = EV / "r87_holder_concentration_probe.json"
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("counts", "rates", "D1", "decision")}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
