# 输入说明: R46 — HS300 正式纳入事件后的可交易漂移。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r46_index_inclusion.py
# 结果 JSON: 同目录 r46_index_inclusion.json。
from __future__ import annotations

import bisect
import hashlib
import importlib.util
import json
import statistics as st
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


r40 = _load("r40", "script_r40_survivorship.py")
r43 = _load("r43", "script_r43_lhb_event.py")
DATA = r40.DATA
START, END = date(2021, 6, 1), date(2026, 6, 29)
COST_2X = 0.0028
WINDOWS = (1, 3, 5, 20, 60)


def _events() -> tuple[pl.DataFrame, dict]:
    snapshots = pl.read_parquet(r40.MEMBERSHIP_CACHE).with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("effective_date").cast(pl.Date),
        pl.col("symbol").cast(pl.Utf8),
    ])
    first_query = snapshots["query_date"].min()
    initial = set(snapshots.filter(pl.col("query_date") == first_query)["symbol"].to_list())
    first = (
        snapshots.sort(["symbol", "query_date"])
        .group_by("symbol", maintain_order=True)
        .agg([
            pl.col("query_date").first().alias("first_query"),
            pl.col("effective_date").first().alias("effective_date"),
        ])
    )
    events = first.filter(
        (~pl.col("symbol").is_in(initial))
        & (pl.col("first_query") >= START)
        & (pl.col("effective_date") <= END)
    ).sort(["effective_date", "symbol"])
    batch = events.group_by("effective_date").len().sort("effective_date")
    audit = {
        "snapshot_count": snapshots["query_date"].n_unique(),
        "initial_symbols": len(initial),
        "additions": events.height,
        "effective_dates": events["effective_date"].n_unique(),
        "batch_sizes": batch["len"].describe().to_dicts(),
        "date_range": [str(events["effective_date"].min()), str(events["effective_date"].max())],
    }
    return events, audit


def _deoverlap(events: pl.DataFrame, trading_dates: list[date]) -> tuple[pl.DataFrame, int]:
    rows, last = [], {}
    for row in events.sort(["symbol", "effective_date"]).iter_rows(named=True):
        idx = bisect.bisect_right(trading_dates, row["effective_date"])
        if idx >= len(trading_dates):
            continue
        if row["symbol"] in last and idx - last[row["symbol"]] < 60:
            continue
        last[row["symbol"]] = idx
        rows.append(row)
    return pl.DataFrame(rows), events.height


def _add_car(events: pl.DataFrame, series: dict, bench: dict) -> tuple[pl.DataFrame, int]:
    rows, missing = [], 0
    for row in events.iter_rows(named=True):
        item = series.get(row["symbol"])
        if not item:
            missing += 1
            continue
        sym_dates, opens = item
        i = bisect.bisect_right(sym_dates, row["effective_date"])
        out = dict(row)
        for horizon in WINDOWS:
            if i >= len(sym_dates) or i + horizon >= len(sym_dates) or opens[i] <= 0:
                out[f"car{horizon}"] = None
                continue
            stock = opens[i + horizon] / opens[i] - 1.0
            bench_ret = 1.0
            for day in sym_dates[i:i + horizon]:
                bench_ret *= 1.0 + bench.get(day, 0.0)
            out[f"car{horizon}"] = stock - (bench_ret - 1.0)
        out["year"] = row["effective_date"].year
        rows.append(out)
    return pl.DataFrame(rows), missing


def _stats(values: list[float]) -> dict:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "t": None}
    mean = st.mean(vals)
    sd = st.pstdev(vals)
    return {
        "n": len(vals),
        "mean": round(mean, 5),
        "median": round(st.median(vals), 5),
        "t": round(mean / (sd / len(vals) ** 0.5), 2) if sd > 0 else None,
    }


def _cluster_stats(events: pl.DataFrame, horizon: int) -> dict:
    col = f"car{horizon}"
    d = events.filter(pl.col(col).is_not_null())
    if d.is_empty():
        return {"n": 0, "mean": None, "median": None, "t": None, "clusters": 0}
    cluster = d.group_by("effective_date").agg(pl.col(col).mean().alias("r")).sort("effective_date")
    vals = cluster["r"].to_list()
    return {**_stats(vals), "clusters": cluster.height}


def _trimmed_median(values: list[float], lower: float = 0.05, upper: float = 0.95) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    lo = vals[min(len(vals) - 1, int(lower * (len(vals) - 1)))]
    hi = vals[min(len(vals) - 1, int(upper * (len(vals) - 1)))]
    trimmed = [v for v in vals if lo <= v <= hi]
    return st.median(trimmed) if trimmed else None


def _digest(events: pl.DataFrame) -> str:
    h = hashlib.sha256()
    for row in events.select(["symbol", "effective_date"]).sort(["effective_date", "symbol"]).iter_rows():
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
        "top": d.group_by("industry_l1").len().sort(["len", "industry_l1"], descending=[True, False]).head(12).to_dicts(),
    }


def main() -> int:
    events, audit = _events()
    memberships, _ = r40.membership_frame()
    symbols = sorted(memberships["symbol"].unique().to_list())
    combined, _ = r40.load_combined_prices(symbols)
    series, bench, dates = r43._series_and_bench(combined, memberships)
    events, _ = _deoverlap(events, dates)
    events, missing_series = _add_car(events, series, bench)
    print("events", events.height, "audit", audit, "missing_series", missing_series, flush=True)

    factor_stats = {str(h): _stats(events[f"car{h}"].to_list()) for h in WINDOWS}
    clusters = {str(h): _cluster_stats(events, h) for h in WINDOWS}
    annual = {}
    for year in range(2021, 2027):
        d = events.filter(pl.col("year") == year)
        annual[str(year)] = {
            "events": d.height,
            "20d": _stats(d["car20"].to_list()) if d.height else {"n": 0},
        }
    positive_years = sum(
        1 for row in annual.values()
        if row["events"] >= 5 and row["20d"].get("median") is not None and row["20d"]["median"] > 0
    )
    car20 = events["car20"].drop_nulls().to_list()
    trimmed20 = _trimmed_median(car20)
    median20 = factor_stats["20"]["median"]
    cluster20 = clusters["20"]
    d1 = bool(
        audit["additions"] >= 100
        and factor_stats["20"]["n"] >= 0.90 * audit["additions"]
        and audit["effective_dates"] >= 10
    )
    e1 = bool(factor_stats["20"]["median"] is not None and factor_stats["20"]["median"] > 0 and factor_stats["20"]["t"] is not None and factor_stats["20"]["t"] > 2)
    e2 = bool(factor_stats["60"]["median"] is not None and factor_stats["60"]["median"] > 0 and factor_stats["60"]["t"] is not None and factor_stats["60"]["t"] > 2)
    e3 = bool(positive_years >= 4)
    e4 = bool(median20 is not None and median20 - COST_2X > 0)
    e5 = bool(
        cluster20["median"] is not None and cluster20["median"] > 0
        and cluster20["t"] is not None and cluster20["t"] > 1.5
        and trimmed20 is not None and trimmed20 > 0
    )
    decision = {"D1": d1, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "all_pass": bool(d1 and e1 and e2 and e3 and e4 and e5)}
    result = {
        "window": {"start": str(START), "end": str(END), "horizons": list(WINDOWS), "cost_2x": COST_2X},
        "audit": audit,
        "data": {
            "events_after_deoverlap": events.height,
            "missing_price_series": missing_series,
            "event_digest": _digest(events),
            "car20_coverage": round(factor_stats["20"]["n"] / events.height, 6) if events.height else 0,
            "industry": _industry(events),
        },
        "event_stats": factor_stats,
        "cluster_stats": clusters,
        "annual": annual,
        "positive_years": positive_years,
        "trimmed_median_car20": round(trimmed20, 5) if trimmed20 is not None else None,
        "median_car20_minus_cost": round(median20 - COST_2X, 5) if median20 is not None else None,
        "decision": decision,
        "recommendation": "R47 event portfolio integration" if decision["all_pass"] else "negative knowledge; do not deploy",
        "caveats": [
            "纳入识别依赖月度 BaoStock 快照, 但事件日使用 official effective_date",
            "仅研究 effective date 之后的漂移, 不包含公告前收益",
            "同一 effective date 的事件不独立, 另报聚类统计",
            "固定 0.28% 往返成本, 未建模冲击成本",
        ],
    }
    path = EV / "r46_index_inclusion.json"
    path.write_text(json.dumps(r43.r40.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("stats", factor_stats, "clusters", clusters, flush=True)
    print("annual", annual, "positive_years", positive_years, "trimmed20", trimmed20, flush=True)
    print("decision", decision, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
