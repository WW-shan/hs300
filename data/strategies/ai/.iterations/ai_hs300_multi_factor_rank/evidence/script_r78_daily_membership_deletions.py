# 输入说明: R78 — 逐日官方 HS300 成员审计 + 剔除事件后反转。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r78_daily_membership_deletions.py
# 原始逐日成员缓存 data/research/r78_daily_membership/ (不入库); 结果 JSON 同目录 r78_daily_membership_deletions.json。
from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import math
import statistics as st
from datetime import date
from itertools import pairwise
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r40 = _load("r40", "script_r40_survivorship.py")
DATA = r40.DATA
CACHE_DIR = DATA / "research" / "r78_daily_membership"
SNAPSHOT_CACHE = CACHE_DIR / "daily_snapshots.parquet"
MEMBER_START = date(2020, 12, 31)
EVENT_START = date(2021, 6, 1)
EVENT_END = date(2026, 6, 30)
MEMBERSHIP_END = date(2026, 10, 14)
WINDOWS = (1, 3, 5, 20, 60)
COST_2X = 0.0028
MIN_MEMBERS = 300
TRANSITION_LOOKAHEAD = 60


def _trading_dates(start: date, end: date) -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    dates = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(
            (pl.col("symbol") == "000300.SH")
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    return [date.fromisoformat(str(value)[:10]) for value in dates]


def _normalize_code(raw: str) -> str | None:
    code = str(raw).strip().lower()
    if code.startswith("sh."):
        return f"{code[3:9]}.SH"
    if code.startswith("sz."):
        return f"{code[3:9]}.SZ"
    if code.startswith("bj."):
        return f"{code[3:9]}.BJ"
    return None


def _read_cache() -> pl.DataFrame:
    if not SNAPSHOT_CACHE.exists():
        return pl.DataFrame(
            schema={
                "query_date": pl.Date,
                "update_date": pl.Date,
                "symbol": pl.String,
                "name": pl.String,
            }
        )
    return pl.read_parquet(SNAPSHOT_CACHE).with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("update_date").cast(pl.Date),
        pl.col("symbol").cast(pl.String),
        pl.col("name").cast(pl.String),
    ])


def _query_snapshot(bs, day: date) -> list[dict]:
    query = bs.query_hs300_stocks(date=str(day))
    if query.error_code != "0":
        raise RuntimeError(f"query_hs300_stocks {day}: {query.error_code} {query.error_msg}")
    rows: list[dict] = []
    while query.next():
        update_date, raw_code, name = query.get_row_data()
        symbol = _normalize_code(raw_code)
        if symbol is None:
            continue
        rows.append({
            "query_date": day,
            "update_date": date.fromisoformat(str(update_date)[:10]),
            "symbol": symbol,
            "name": str(name or ""),
        })
    if len(rows) != MIN_MEMBERS or len({row["symbol"] for row in rows}) != MIN_MEMBERS:
        raise RuntimeError(f"query_hs300_stocks {day}: expected {MIN_MEMBERS}, got {len(rows)}")
    return rows


def _ensure_daily_snapshots(dates: list[date]) -> tuple[pl.DataFrame, dict]:
    import baostock as bs

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_cache()
    done = set(existing["query_date"].unique().to_list()) if existing.height else set()
    missing = [day for day in dates if day not in done]
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock 登录失败: {login.error_code} {login.error_msg}")
    try:
        buffer: list[dict] = []
        for index, day in enumerate(missing, start=1):
            buffer.extend(_query_snapshot(bs, day))
            if index % 50 == 0 or index == len(missing):
                chunk = pl.DataFrame(buffer).with_columns([
                    pl.col("query_date").cast(pl.Date),
                    pl.col("update_date").cast(pl.Date),
                ])
                merged = pl.concat([existing, chunk], how="diagonal_relaxed") if existing.height else chunk
                existing = merged.unique(subset=["query_date", "symbol"], keep="last").sort(
                    ["query_date", "symbol"]
                )
                existing.write_parquet(SNAPSHOT_CACHE)
                print(f"membership snapshots {index}/{len(missing)}", flush=True)
                buffer.clear()
    finally:
        bs.logout()

    frame = _read_cache().filter(pl.col("query_date").is_in(dates))
    per_day = frame.group_by("query_date").agg([
        pl.len().alias("rows"),
        pl.col("symbol").n_unique().alias("symbols"),
        pl.col("update_date").max().alias("max_update_date"),
    ]).sort("query_date")
    expected = set(dates)
    observed = set(per_day["query_date"].to_list())
    audit = {
        "requested_dates": len(dates),
        "available_dates": len(observed),
        "missing_dates": sorted(expected - observed),
        "extra_dates": len(observed - expected),
        "rows": frame.height,
        "symbols": frame["symbol"].n_unique(),
        "min_rows_per_date": int(per_day["rows"].min()) if per_day.height else 0,
        "max_rows_per_date": int(per_day["rows"].max()) if per_day.height else 0,
        "min_symbols_per_date": int(per_day["symbols"].min()) if per_day.height else 0,
        "max_symbols_per_date": int(per_day["symbols"].max()) if per_day.height else 0,
        "future_update_rows": frame.filter(pl.col("update_date") > pl.col("query_date")).height,
        "date_min": str(frame["query_date"].min()) if frame.height else None,
        "date_max": str(frame["query_date"].max()) if frame.height else None,
    }
    return frame, audit


def _daily_sets(snapshots: pl.DataFrame) -> dict[date, set[str]]:
    return {
        keys[0] if isinstance(keys, tuple) else keys: set(group["symbol"].to_list())
        for keys, group in snapshots.group_by("query_date", maintain_order=True)
    }


def _monthly_sets_for_dates(memberships: pl.DataFrame, days: list[date]) -> dict[date, set[str]]:
    effective_dates = sorted(memberships["effective_date"].unique().to_list())
    by_effective = {
        keys[0] if isinstance(keys, tuple) else keys: set(group["symbol"].to_list())
        for keys, group in memberships.group_by("effective_date", maintain_order=True)
    }
    out: dict[date, set[str]] = {}
    for day in days:
        index = bisect.bisect_right(effective_dates, day) - 1
        out[day] = by_effective[effective_dates[index]] if index >= 0 else set()
    return out


def _membership_audit(
    snapshots: pl.DataFrame,
    memberships: pl.DataFrame,
    days: list[date],
) -> dict:
    exact = _daily_sets(snapshots)
    monthly = _monthly_sets_for_dates(memberships, days)
    rows = []
    total_exact = total_overlap = 0
    for day in days:
        left, right = exact.get(day, set()), monthly.get(day, set())
        symdiff = len(left ^ right)
        total_exact += len(left)
        total_overlap += len(left & right)
        rows.append({
            "date": str(day),
            "exact": len(left),
            "monthly": len(right),
            "symmetric_diff": symdiff,
            "jaccard": round(len(left & right) / len(left | right), 6) if left | right else 1.0,
        })
    d = pl.DataFrame(rows)
    mismatch = d.filter(pl.col("symmetric_diff") > 0)
    sorted_mismatch = mismatch.sort("symmetric_diff", descending=True)
    return {
        "days": d.height,
        "equal_days": d.height - mismatch.height,
        "mismatch_days": mismatch.height,
        "mismatch_day_rate": round(mismatch.height / d.height, 6) if d.height else None,
        "symbol_day_accuracy": round(total_overlap / total_exact, 6) if total_exact else None,
        "mean_symmetric_diff": round(float(d["symmetric_diff"].mean()), 4) if d.height else None,
        "max_symmetric_diff": int(d["symmetric_diff"].max()) if d.height else 0,
        "mean_jaccard": round(float(d["jaccard"].mean()), 6) if d.height else None,
        "worst_days": sorted_mismatch.head(30).to_dicts(),
    }


def _transitions(days: list[date], exact: dict[date, set[str]]) -> tuple[pl.DataFrame, dict]:
    rows = []
    for previous, current in pairwise(days):
        left, right = exact.get(previous, set()), exact.get(current, set())
        for symbol in sorted(left - right):
            rows.append({
                "event_date": current,
                "previous_date": previous,
                "symbol": symbol,
                "direction": "deletion",
            })
        for symbol in sorted(right - left):
            rows.append({
                "event_date": current,
                "previous_date": previous,
                "symbol": symbol,
                "direction": "addition",
            })
    frame = pl.DataFrame(rows)
    per_date = (
        frame.filter(pl.col("direction") == "deletion")
        .group_by("event_date")
        .agg([
            pl.len().alias("deletions"),
            pl.col("previous_date").max().alias("previous_date"),
        ])
        .sort("event_date")
    )
    batch_sizes = per_date["deletions"].to_list()
    audit = {
        "transition_rows": frame.height,
        "deletion_events": frame.filter(pl.col("direction") == "deletion").height,
        "addition_events": frame.filter(pl.col("direction") == "addition").height,
        "transition_dates": per_date.height,
        "batch_min": min(batch_sizes) if batch_sizes else 0,
        "batch_median": st.median(batch_sizes) if batch_sizes else None,
        "batch_max": max(batch_sizes) if batch_sizes else 0,
        "batch_distribution": per_date.group_by("deletions").len().sort("deletions").to_dicts(),
        "transition_dates_detail": per_date.to_dicts(),
    }
    return frame, audit


def _deoverlap(events: pl.DataFrame, days: list[date]) -> tuple[pl.DataFrame, int]:
    rows, last = [], {}
    for row in events.sort(["symbol", "event_date"]).iter_rows(named=True):
        index = bisect.bisect_left(days, row["event_date"])
        if row["symbol"] in last and index - last[row["symbol"]] < TRANSITION_LOOKAHEAD:
            continue
        last[row["symbol"]] = index
        rows.append(row)
    return pl.DataFrame(rows), events.height


def _exact_benchmark(combined: pl.DataFrame, exact: dict[date, set[str]]) -> tuple[dict[date, float], dict]:
    panel = combined.select(["symbol", "date", "open"]).sort(["symbol", "date"])
    panel = panel.with_columns(pl.col("open").shift(-1).over("symbol").alias("next_open"))
    rows = [
        {"date": day, "symbol": symbol}
        for day, symbols in exact.items()
        for symbol in symbols
    ]
    allowed = pl.DataFrame(rows, schema={"date": pl.Date, "symbol": pl.String})
    member_panel = panel.join(allowed, on=["date", "symbol"], how="inner")
    market = (
        member_panel.filter(
            pl.col("next_open").is_not_null()
            & (pl.col("open") > 0)
            & (pl.col("next_open") > 0)
        )
        .with_columns((pl.col("next_open") / pl.col("open") - 1.0).alias("r"))
        .group_by("date")
        .agg([
            pl.len().alias("members"),
            pl.col("r").mean().alias("mkt_r"),
        ])
        .sort("date")
    )
    valid = market.filter(pl.col("mkt_r").is_not_null())
    audit = {
        "days": valid.height,
        "min_members": int(valid["members"].min()) if valid.height else 0,
        "median_members": float(valid["members"].median()) if valid.height else None,
        "max_members": int(valid["members"].max()) if valid.height else 0,
    }
    return dict(zip(valid["date"].to_list(), valid["mkt_r"].to_list(), strict=False)), audit


def _series(combined: pl.DataFrame) -> dict[str, tuple[list[date], list[float]]]:
    panel = combined.select(["symbol", "date", "open"]).sort(["symbol", "date"])
    out = {}
    for keys, group in panel.group_by("symbol", maintain_order=True):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        out[symbol] = (group["date"].to_list(), group["open"].to_list())
    return out


def _add_car(
    events: pl.DataFrame,
    series: dict[str, tuple[list[date], list[float]]],
    bench: dict[date, float],
) -> tuple[pl.DataFrame, dict]:
    rows, missing_series, missing_bench = [], 0, 0
    for row in events.iter_rows(named=True):
        item = series.get(row["symbol"])
        if not item:
            missing_series += 1
            continue
        sym_dates, opens = item
        index = bisect.bisect_right(sym_dates, row["event_date"])
        if index >= len(sym_dates):
            missing_series += 1
            continue
        out = dict(row)
        for horizon in WINDOWS:
            if index + horizon >= len(sym_dates) or opens[index] <= 0 or opens[index + horizon] <= 0:
                out[f"car{horizon}"] = None
                continue
            stock = opens[index + horizon] / opens[index] - 1.0
            bench_return = 1.0
            for day in sym_dates[index:index + horizon]:
                value = bench.get(day)
                if value is None:
                    missing_bench += 1
                    value = 0.0
                bench_return *= 1.0 + value
            out[f"car{horizon}"] = stock - (bench_return - 1.0)
        out["entry_date"] = sym_dates[index]
        out["entry_delay_trading_days"] = index - bisect.bisect_left(sym_dates, row["event_date"])
        out["year"] = row["event_date"].year
        rows.append(out)
    audit = {
        "events_after_deoverlap": events.height,
        "events_with_series": len(rows),
        "missing_series": missing_series,
        "missing_benchmark_days": missing_bench,
    }
    return pl.DataFrame(rows), audit


def _stats(values: list[float | None]) -> dict:
    vals = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "t": None, "std": None}
    mean = st.mean(vals)
    std = st.pstdev(vals)
    return {
        "n": len(vals),
        "mean": round(mean, 6),
        "median": round(st.median(vals), 6),
        "t": round(mean / (std / len(vals) ** 0.5), 2) if std > 0 else None,
        "std": round(std, 6),
    }


def _cluster_stats(events: pl.DataFrame, horizon: int) -> dict:
    column = f"car{horizon}"
    d = events.filter(pl.col(column).is_not_null())
    if d.is_empty():
        return {"n": 0, "mean": None, "median": None, "t": None, "clusters": 0}
    cluster = (
        d.group_by("event_date")
        .agg([
            pl.col(column).mean().alias("r"),
            pl.len().alias("events"),
        ])
        .sort("event_date")
    )
    return {**_stats(cluster["r"].to_list()), "clusters": cluster.height}


def _trimmed_median(values: list[float | None], lower: float = 0.05, upper: float = 0.95) -> float | None:
    vals = sorted(float(value) for value in values if value is not None)
    if not vals:
        return None
    lo = vals[min(len(vals) - 1, int(lower * (len(vals) - 1)))]
    hi = vals[min(len(vals) - 1, int(upper * (len(vals) - 1)))]
    trimmed = [value for value in vals if lo <= value <= hi]
    return st.median(trimmed) if trimmed else None


def _digest(events: pl.DataFrame) -> str:
    h = hashlib.sha256()
    for row in events.select(["symbol", "event_date"]).sort(["event_date", "symbol"]).iter_rows():
        h.update(repr(row).encode())
    return h.hexdigest()


def _industry(events: pl.DataFrame) -> dict:
    try:
        from app.services.industry_map import load_industry_l1

        industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    except Exception as exc:  # noqa: BLE001 - report-only
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    d = events.join(industry, on="symbol", how="left")
    return {
        "coverage": round(float(d["industry_l1"].is_not_null().mean()), 4),
        "top": (
            d.group_by("industry_l1")
            .len()
            .sort(["len", "industry_l1"], descending=[True, False])
            .head(12)
            .to_dicts()
        ),
    }


def main() -> int:
    days = _trading_dates(MEMBER_START, MEMBERSHIP_END)
    snapshots, snapshot_audit = _ensure_daily_snapshots(days)
    exact = _daily_sets(snapshots)
    monthly, _ = r40.membership_frame()
    membership_audit = _membership_audit(snapshots, monthly, days)
    all_transitions, transition_audit = _transitions(days, exact)
    deletions = all_transitions.filter(
        (pl.col("direction") == "deletion")
        & (pl.col("event_date") >= EVENT_START)
        & (pl.col("event_date") <= EVENT_END)
    )
    deletions, before_deoverlap = _deoverlap(deletions, days)
    symbols = sorted(snapshots["symbol"].unique().to_list())
    combined, price_audit = r40.load_combined_prices(symbols)
    price_audit["bootstrap_trade_status_values"] = sorted(
        price_audit.get("bootstrap_trade_status_values") or [],
        key=lambda row: str(row.get("trade_status")),
    )
    bench, bench_audit = _exact_benchmark(combined, exact)
    series = _series(combined)
    events, event_audit = _add_car(deletions, series, bench)
    print("membership", snapshot_audit, "monthly", membership_audit, flush=True)
    print("transitions", transition_audit, "deletions", before_deoverlap, "events", events.height, flush=True)

    event_stats = {str(horizon): _stats(events[f"car{horizon}"].to_list()) for horizon in WINDOWS}
    clusters = {str(horizon): _cluster_stats(events, horizon) for horizon in WINDOWS}
    annual = {}
    for year in range(2021, 2027):
        d = events.filter(pl.col("year") == year)
        annual[str(year)] = {
            "events": d.height,
            "20d": _stats(d["car20"].to_list()) if d.height else {"n": 0},
        }
    positive_years = sum(
        1
        for row in annual.values()
        if row["events"] >= 5
        and row["20d"].get("median") is not None
        and row["20d"]["median"] > 0
    )
    car20 = events["car20"].drop_nulls().to_list()
    trimmed20 = _trimmed_median(car20)
    median20 = event_stats["20"]["median"]
    cluster20 = clusters["20"]
    d1 = bool(
        snapshot_audit["requested_dates"] >= 1300
        and snapshot_audit["available_dates"] == snapshot_audit["requested_dates"]
        and snapshot_audit["min_rows_per_date"] == MIN_MEMBERS
        and snapshot_audit["max_rows_per_date"] == MIN_MEMBERS
        and snapshot_audit["min_symbols_per_date"] == MIN_MEMBERS
        and snapshot_audit["max_symbols_per_date"] == MIN_MEMBERS
        and snapshot_audit["future_update_rows"] == 0
        and before_deoverlap >= 100
        and transition_audit["transition_dates"] >= 10
        and event_stats["20"]["n"] >= 0.90 * before_deoverlap
    )
    e1 = bool(
        event_stats["20"]["median"] is not None
        and event_stats["20"]["median"] > 0
        and event_stats["20"]["t"] is not None
        and event_stats["20"]["t"] > 2
    )
    e2 = bool(
        event_stats["60"]["median"] is not None
        and event_stats["60"]["median"] > 0
        and event_stats["60"]["t"] is not None
        and event_stats["60"]["t"] > 2
    )
    e3 = bool(positive_years >= 4)
    e4 = bool(median20 is not None and median20 - COST_2X > 0)
    e5 = bool(
        cluster20["median"] is not None
        and cluster20["median"] > 0
        and cluster20["t"] is not None
        and cluster20["t"] > 1.5
        and trimmed20 is not None
        and trimmed20 > 0
    )
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
        "E5": e5,
        "all_pass": bool(d1 and e1 and e2 and e3 and e4 and e5),
    }
    result = {
        "window": {
            "membership_start": str(MEMBER_START),
            "event_start": str(EVENT_START),
            "event_end": str(EVENT_END),
            "membership_end": str(MEMBERSHIP_END),
            "horizons": list(WINDOWS),
            "cost_2x": COST_2X,
        },
        "snapshot_audit": snapshot_audit,
        "membership_audit": membership_audit,
        "transition_audit": transition_audit,
        "price_audit": price_audit,
        "benchmark_audit": bench_audit,
        "event_audit": {**event_audit, "before_deoverlap": before_deoverlap, "event_digest": _digest(events)},
        "event_stats": event_stats,
        "cluster_stats": clusters,
        "annual": annual,
        "positive_years": positive_years,
        "trimmed_median_car20": round(trimmed20, 6) if trimmed20 is not None else None,
        "median_car20_minus_cost": round(median20 - COST_2X, 6) if median20 is not None else None,
        "entry_delay": _stats(events["entry_delay_trading_days"].to_list()),
        "industry": _industry(events),
        "decision": decision,
        "recommendation": "R79 event portfolio integration" if decision["all_pass"] else "negative knowledge; do not deploy",
        "caveats": [
            "逐日成员依赖 BaoStock 历史查询, 不是中证指数官方原始调样公告",
            "事件日由相邻交易日名单差识别, 数据供应商单日异常会被视为过渡",
            "持有期按个股自身可得开盘序列计交易日; 停牌可能延后实际退出",
            "固定 0.28% 往返成本, 未建模冲击成本与涨跌停不可成交",
            "月度快照误差只作审计, 事件识别全部使用逐日快照",
        ],
    }
    path = EV / "r78_daily_membership_deletions.json"
    path.write_text(
        json.dumps(r40.r22.compact(result), ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
    print("stats", event_stats, "clusters", clusters, flush=True)
    print("annual", annual, "positive_years", positive_years, "trimmed20", trimmed20, flush=True)
    print("decision", decision, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
