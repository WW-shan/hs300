# 输入说明: R87 — 新浪前十大流通股东历史/PIT/月度覆盖率审计（不看收益）。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r87_holder_concentration_sina_probe.py
# 结果 JSON: 同目录 r87_holder_concentration_sina_probe.json。原始缓存 data/research/r87_sina_holder/ (不提交)。
from __future__ import annotations

import calendar
import contextlib
import hashlib
import importlib.util
import io
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


east = _load("r87_east", "script_r87_holder_concentration_probe.py")
DATA = east.DATA
CACHE = DATA / "research" / "r87_sina_holder_full"
EAST_CACHE = DATA / "research" / "r87_holder_concentration"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
FETCH_PERIOD_START = date(2020, 3, 31)
PERIOD_START = date(2021, 3, 31)
PERIOD_END = date(2026, 6, 30)
WORKERS = 4
MAX_AGE_DAYS = 200
MIN_FACTOR_COVERAGE = 0.85


def _normalize(raw, symbol: str) -> pl.DataFrame:
    if raw is None or len(raw) == 0:
        return pl.DataFrame()
    columns = {
        "截止日期": "period_end",
        "公告日期": "announce_date",
        "编号": "holder_rank",
        "股东名称": "holder_name",
        "持股数量": "hold_shares",
        "占流通股比例": "free_float_ratio",
        "股本性质": "holder_type",
    }
    available = [column for column in columns if column in raw.columns]
    if len(available) < 6:
        raise RuntimeError(f"missing columns: {list(raw.columns)}")
    frame = pl.from_pandas(raw[available].copy()).rename(columns)
    frame = frame.with_columns([
        pl.lit(symbol).alias("symbol"),
        pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False),
        pl.col("holder_rank").cast(pl.Int64, strict=False),
        pl.col("hold_shares").cast(pl.Float64, strict=False),
        pl.col("free_float_ratio").cast(pl.Float64, strict=False),
    ])
    return (
        frame.filter(
            (pl.col("period_end") >= FETCH_PERIOD_START)
            & (pl.col("period_end") <= PERIOD_END)
            & pl.col("holder_rank").is_between(1, 10, closed="both")
        )
        .unique(subset=["symbol", "period_end", "holder_rank", "holder_name"], keep="last")
        .sort(["period_end", "holder_rank"])
    )


def _fetch_symbol(symbol: str) -> dict:
    import akshare as ak

    path = CACHE / f"{symbol}.parquet"
    if path.exists() and path.stat().st_size > 0:
        return {"symbol": symbol, "ok": True, "rows": pl.read_parquet(path).height, "error": ""}
    last_error = "unknown"
    for attempt in range(3):
        try:
            sink = io.StringIO()
            with contextlib.redirect_stderr(sink):
                raw = ak.stock_circulate_stock_holder(symbol=symbol[:6])
            frame = _normalize(raw, symbol)
            if frame.height == 0:
                return {"symbol": symbol, "ok": False, "rows": 0, "error": "empty"}
            tmp = path.with_suffix(".tmp")
            frame.write_parquet(tmp)
            tmp.replace(path)
            return {"symbol": symbol, "ok": True, "rows": frame.height, "error": ""}
        except Exception as exc:  # noqa: BLE001 - source audit records failures
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    return {"symbol": symbol, "ok": False, "rows": 0, "error": last_error}


def _fetch_all(symbols: list[str]) -> tuple[pl.DataFrame, list[dict]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    audits_by_symbol: dict[str, dict] = {}
    missing = [symbol for symbol in symbols if not (CACHE / f"{symbol}.parquet").exists()]
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_fetch_symbol, symbol): symbol for symbol in missing}
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            audits_by_symbol[result["symbol"]] = result
            if index % 5 == 0 or index == len(futures):
                print(f"sina holder probe {index}/{len(futures)}", flush=True)
    for symbol in symbols:
        if symbol not in audits_by_symbol and (CACHE / f"{symbol}.parquet").exists():
            audits_by_symbol[symbol] = {
                "symbol": symbol,
                "ok": True,
                "rows": pl.read_parquet(CACHE / f"{symbol}.parquet").height,
                "error": "cache",
            }
    frames = [
        pl.read_parquet(CACHE / f"{symbol}.parquet")
        for symbol in symbols
        if (CACHE / f"{symbol}.parquet").exists()
    ]
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    return raw, sorted(audits_by_symbol.values(), key=lambda item: item["symbol"])


def _aggregate(raw: pl.DataFrame) -> pl.DataFrame:
    if raw.height == 0:
        return pl.DataFrame()
    return (
        raw.group_by(["symbol", "period_end"])
        .agg([
            pl.col("announce_date").drop_nulls().min().alias("announce_date"),
            pl.len().alias("holder_rows"),
            pl.col("holder_rank").n_unique().alias("rank_count"),
            pl.col("free_float_ratio").is_not_null().sum().alias("ratio_nonnull"),
            pl.col("free_float_ratio").sum().round(8).alias("ratio_sum"),
            pl.col("free_float_ratio").filter(pl.col("holder_rank") == 1).max().alias("ratio_top1"),
        ])
        .with_columns(
            (pl.col("ratio_sum") / 100.0).alias("_unused")
        )
        .drop("_unused")
        .sort(["symbol", "period_end"])
    )


def _month_end_members() -> pl.DataFrame:
    snapshots = pl.read_parquet(east.DAILY_MEMBERSHIP).filter(
        (pl.col("query_date") >= START) & (pl.col("query_date") <= END)
    )
    month_last = (
        snapshots.with_columns(pl.col("query_date").dt.strftime("%Y-%m").alias("_month"))
        .group_by("_month")
        .agg(pl.col("query_date").max().alias("query_date"))
        .sort("query_date")
    )
    return (
        snapshots.join(month_last, on="query_date")
        .select([pl.col("query_date").alias("date"), "symbol"])
        .sort(["date", "symbol"])
    )


def _period_dates() -> list[date]:
    values = []
    for year in range(PERIOD_START.year, PERIOD_END.year + 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            value = date(year, month, day)
            if PERIOD_START <= value <= PERIOD_END:
                values.append(value)
    return values


def _shift_period(period: date, years: int = 0, quarters: int = 0) -> date:
    month = period.month + quarters * 3
    year = period.year + years
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    day = calendar.monthrange(year, month)[1]
    return date(year, month, day)


def _monthly_coverage(aggregates: pl.DataFrame, symbols: list[str]) -> tuple[list[dict], dict]:
    members = _month_end_members().filter(pl.col("symbol").is_in(symbols))
    usable = aggregates.filter(
        (pl.col("rank_count") == 10)
        & (pl.col("ratio_nonnull") == 10)
        & pl.col("announce_date").is_not_null()
        & (pl.col("ratio_sum") >= 0)
        & (pl.col("ratio_sum") <= 100.01)
    )
    lookup = {
        (row["symbol"], row["period_end"]): row
        for row in usable.iter_rows(named=True)
    }
    rows: list[dict] = []
    for member_day in members.partition_by("date", maintain_order=True):
        day = member_day["date"][0]
        eligible = member_day.height
        level = qoq = yoy = 0
        for symbol in member_day["symbol"].to_list():
            candidates = [
                row for (candidate_symbol, _), row in lookup.items()
                if candidate_symbol == symbol
                and row["announce_date"] < day
                and 0 <= (day - row["period_end"]).days <= MAX_AGE_DAYS
            ]
            if not candidates:
                continue
            current = max(candidates, key=lambda row: row["announce_date"])
            level += 1
            prior_q = lookup.get((symbol, _shift_period(current["period_end"], quarters=-1)))
            prior_y = lookup.get((symbol, _shift_period(current["period_end"], years=-1)))
            if prior_q is not None and prior_q["announce_date"] < day and prior_q["announce_date"] <= current["announce_date"]:
                qoq += 1
            if prior_y is not None and prior_y["announce_date"] < day and prior_y["announce_date"] <= current["announce_date"]:
                yoy += 1
        rows.append({
            "date": str(day),
            "eligible": eligible,
            "level_valid": level,
            "qoq_valid": qoq,
            "yoy_valid": yoy,
        })
    frame = pl.DataFrame(rows)
    stats = {}
    for factor, column in (
        ("holder_top10_free_float_share", "level_valid"),
        ("holder_top10_share_chg_qoq", "qoq_valid"),
        ("holder_top10_share_chg_yoy", "yoy_valid"),
    ):
        coverage = frame[column] / frame["eligible"]
        stats[factor] = {
            "mean_coverage": round(float(coverage.mean()), 6),
            "min_valid": int(frame[column].min()),
            "months": frame.height,
            "months_coverage_ge_70pct": int((coverage >= 0.70).sum()),
        }
    return rows, stats


def _cross_source(sina: pl.DataFrame) -> dict:
    frames = [
        pl.read_parquet(path)
        for path in sorted(EAST_CACHE.glob("*.parquet"))
        if path.stat().st_size > 0
    ]
    if not frames:
        return {"n": 0, "error": "missing Eastmoney cache"}
    em = pl.concat(frames, how="diagonal_relaxed").filter(
        pl.col("request_ok")
        & (pl.col("rows") == 10)
        & (pl.col("ratio_nonnull") == 10)
        & (pl.col("ratio_sum") >= 0)
        & (pl.col("ratio_sum") <= 100.01)
    ).select(["symbol", "period_end", pl.col("ratio_sum").alias("east_ratio_sum")])
    joined = (
        sina.filter((pl.col("rank_count") == 10) & (pl.col("ratio_nonnull") == 10))
        .select(["symbol", "period_end", "ratio_sum"])
        .join(em, on=["symbol", "period_end"], how="inner")
        .with_columns((pl.col("ratio_sum") - pl.col("east_ratio_sum")).abs().alias("_abs_diff"))
    )
    if joined.height < 10:
        return {"n": joined.height, "error": "insufficient overlap"}
    return {
        "n": joined.height,
        "pearson": round(float(joined.select(pl.corr("ratio_sum", "east_ratio_sum")).item()), 6),
        "mean_abs_diff_pp": round(float(joined["_abs_diff"].mean()), 6),
        "median_abs_diff_pp": round(float(joined["_abs_diff"].median()), 6),
        "p95_abs_diff_pp": round(float(joined["_abs_diff"].quantile(0.95)), 6),
        "max_abs_diff_pp": round(float(joined["_abs_diff"].max()), 6),
    }


def main() -> int:
    symbols, labels = east._sample_symbols()
    raw, fetch_audit = _fetch_all(symbols)
    aggregates = _aggregate(raw)
    monthly, coverage = _monthly_coverage(aggregates, symbols)
    cross = _cross_source(aggregates)
    periods = _period_dates()
    expected_symbol_periods = len(symbols) * len(periods)
    success_symbols = sum(1 for row in fetch_audit if row["ok"])
    complete = aggregates.filter(
        (pl.col("rank_count") == 10)
        & (pl.col("ratio_nonnull") == 10)
        & pl.col("announce_date").is_not_null()
        & (pl.col("ratio_sum") >= 0)
        & (pl.col("ratio_sum") <= 100.01)
        & pl.col("period_end").is_in(periods)
    )
    period_coverage = complete.height / expected_symbol_periods
    announce_coverage = (
        complete.height / aggregates.filter(pl.col("period_end").is_in(periods)).height
        if aggregates.height else 0.0
    )
    checks = {
        "fetch_success_rate": {
            "value": round(success_symbols / len(symbols), 6),
            "threshold": 0.90,
            "pass": success_symbols / len(symbols) >= 0.90,
        },
        "complete_symbol_period_rate": {
            "value": round(period_coverage, 6),
            "threshold": 0.90,
            "pass": period_coverage >= 0.90,
        },
        "announcement_date_coverage": {
            "value": round(announce_coverage, 6),
            "threshold": 0.95,
            "pass": announce_coverage >= 0.95,
        },
        "cross_source_pearson": {
            "value": cross.get("pearson"),
            "threshold": 0.95,
            "pass": cross.get("pearson") is not None and cross["pearson"] >= 0.95,
        },
        "cross_source_median_abs_diff_pp": {
            "value": cross.get("median_abs_diff_pp"),
            "threshold": 2.0,
            "pass": cross.get("median_abs_diff_pp") is not None and cross["median_abs_diff_pp"] <= 2.0,
        },
        "monthly_level_coverage": {
            "value": coverage["holder_top10_free_float_share"]["mean_coverage"],
            "threshold": MIN_FACTOR_COVERAGE,
            "pass": coverage["holder_top10_free_float_share"]["mean_coverage"] >= MIN_FACTOR_COVERAGE,
        },
        "monthly_qoq_coverage": {
            "value": coverage["holder_top10_share_chg_qoq"]["mean_coverage"],
            "threshold": MIN_FACTOR_COVERAGE,
            "pass": coverage["holder_top10_share_chg_qoq"]["mean_coverage"] >= MIN_FACTOR_COVERAGE,
        },
        "monthly_yoy_coverage": {
            "value": coverage["holder_top10_share_chg_yoy"]["mean_coverage"],
            "threshold": MIN_FACTOR_COVERAGE,
            "pass": coverage["holder_top10_share_chg_yoy"]["mean_coverage"] >= MIN_FACTOR_COVERAGE,
        },
    }
    d1 = all(item["pass"] for item in checks.values())
    distributions = (
        complete.select([
            pl.col("ratio_sum").min().alias("ratio_sum_min"),
            pl.col("ratio_sum").quantile(0.10).alias("ratio_sum_p10"),
            pl.col("ratio_sum").median().alias("ratio_sum_median"),
            pl.col("ratio_sum").quantile(0.90).alias("ratio_sum_p90"),
            pl.col("ratio_sum").max().alias("ratio_sum_max"),
            (pl.col("announce_date") - pl.col("period_end")).dt.total_days().min().alias("announce_lag_min"),
            (pl.col("announce_date") - pl.col("period_end")).dt.total_days().median().alias("announce_lag_median"),
            (pl.col("announce_date") - pl.col("period_end")).dt.total_days().max().alias("announce_lag_max"),
        ]).to_dicts()[0]
        if complete.height else {}
    )
    result = {
        "purpose": "R87 Sina top-10 free-float holder history/PIT/monthly coverage audit; no returns inspected",
        "window": {"start": str(START), "end": str(END), "warmup_period_start": str(FETCH_PERIOD_START), "period_start": str(PERIOD_START), "period_end": str(PERIOD_END)},
        "sample": {
            "count": len(symbols),
            "symbols": symbols,
            "strata": {
                label: sorted(symbol for symbol in symbols if labels[symbol] == label)
                for label in sorted(set(labels.values()))
            },
            "selection": "same deterministic stratified sample as Eastmoney R87 probe",
        },
        "fetch": {
            "success_symbols": success_symbols,
            "failed_symbols": [row for row in fetch_audit if not row["ok"]],
            "audit": fetch_audit,
        },
        "periods": {
            "count": len(periods),
            "aggregate_rows": aggregates.height,
            "complete_rows": complete.height,
            "expected_symbol_periods": expected_symbol_periods,
        },
        "checks": checks,
        "coverage": coverage,
        "cross_source_eastmoney": cross,
        "distributions": {
            key: (round(float(value), 8) if isinstance(value, float) else value)
            for key, value in distributions.items()
        },
        "monthly_detail": monthly,
        "D1": d1,
        "decision": "proceed to R87 pre-registration and full-history factor screen" if d1 else "data gap; do not compute R87 factors",
        "caveat": "Sina holder pages use announcement dates directly; only symbol-periods with exactly ten ranked and ten non-null free-float ratios are usable. Cross-source check uses Eastmoney overlap only.",
    }
    path = EV / "r87_holder_concentration_sina_probe.json"
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result["sha256_canonical"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"fetch": result["fetch"], "periods": result["periods"], "checks": checks, "coverage": coverage, "cross": cross, "D1": d1}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
