# 输入说明: R49 — 指数纳入后跑输的风格匹配验证 + 60 日排除叠加 (事后假设)。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r49_index_overlay.py
# 结果 JSON: 同目录 r49_index_overlay.json。
from __future__ import annotations

import bisect
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
r46 = _load("r46", "script_r46_index_inclusion.py")
r43 = _load("r43", "script_r43_lhb_event.py")
r42 = _load("r42", "script_r42_historical_factor_screen.py")
r30 = _load("r30", "script_r30_final.py")
r22 = r42.r22
COST_2X = 0.0028
EXCLUDE_DAYS = 60
MATCH_COUNT = 5
MIN_CONTROLS = 3


def _panel_features(panel: pl.DataFrame) -> pl.DataFrame:
    return (
        panel.select(["symbol", "date", "close", "raw_close", "float_mv"])
        .sort(["symbol", "date"])
        .with_columns([
            (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias("mom60"),
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret1"),
            pl.col("float_mv").log().alias("log_mv"),
        ])
        .with_columns(pl.col("_ret1").rolling_std(60).over("symbol").alias("vol60"))
        .select(["symbol", "date", "mom60", "vol60", "log_mv"])
    )


def _event_features(events: pl.DataFrame, features: pl.DataFrame, trading_dates: list[date], panel: pl.DataFrame, snapshots: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    rows, skipped = [], {"no_prev_day": 0, "no_event_fea": 0, "too_few_controls": 0}
    event_dates = {}
    for keys, group in events.group_by("symbol"):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        event_dates[symbol] = sorted(group["effective_date"].to_list())
    feature_dates = set(features["date"].unique().to_list())
    for event in events.iter_rows(named=True):
        day = event["effective_date"]
        pos = bisect.bisect_left(trading_dates, day)
        if pos <= 0:
            skipped["no_prev_day"] += 1
            continue
        prev = trading_dates[pos - 1]
        if prev not in feature_dates:
            skipped["no_prev_day"] += 1
            continue
        day_features = features.filter(pl.col("date") == prev)
        event_fea = day_features.filter(pl.col("symbol") == event["symbol"])
        if event_fea.is_empty() or event_fea["mom60"].null_count() or event_fea["vol60"].null_count() or event_fea["log_mv"].null_count():
            skipped["no_event_fea"] += 1
            continue
        visible = snapshots.filter(pl.col("query_date") <= day)
        if visible.is_empty():
            skipped["no_prev_day"] += 1
            continue
        latest = visible["query_date"].max()
        members = visible.filter(pl.col("query_date") == latest)["symbol"].to_list()
        event_idx = pos
        recent = []
        for symbol, dates in event_dates.items():
            if symbol == event["symbol"]:
                continue
            for d in dates:
                p = bisect.bisect_left(trading_dates, d)
                if 0 <= event_idx - p < 120:
                    recent.append(symbol)
                    break
        candidates = day_features.filter(
            pl.col("symbol").is_in(members)
            & (pl.col("symbol") != event["symbol"])
            & (~pl.col("symbol").is_in(recent))
            & pl.col("mom60").is_not_null()
            & pl.col("vol60").is_not_null()
            & pl.col("log_mv").is_not_null()
        )
        if candidates.height < MIN_CONTROLS:
            skipped["too_few_controls"] += 1
            continue
        means = {c: candidates[c].mean() for c in ("mom60", "vol60", "log_mv")}
        stds = {c: candidates[c].std() or 1.0 for c in ("mom60", "vol60", "log_mv")}
        ef = {c: float(event_fea[c][0]) for c in ("mom60", "vol60", "log_mv")}
        scored = candidates.with_columns([
            (((pl.col(c) - means[c]) / stds[c]) - ((ef[c] - means[c]) / stds[c])).pow(2).alias(f"_{c}_d")
            for c in ("mom60", "vol60", "log_mv")
        ]).with_columns(
            (pl.col("_mom60_d") + pl.col("_vol60_d") + pl.col("_log_mv_d")).alias("distance")
        ).sort(["distance", "symbol"]).head(MATCH_COUNT)
        for control in scored["symbol"].to_list():
            rows.append({
                "pair_id": f"{event['symbol']}|{day}",
                "event_symbol": event["symbol"],
                "event_date": day,
                "control_symbol": control,
            })
    out = pl.DataFrame(rows) if rows else pl.DataFrame(schema={
        "pair_id": pl.Utf8, "event_symbol": pl.Utf8, "event_date": pl.Date, "control_symbol": pl.Utf8,
    })
    return out, skipped


def _event_cars(events: pl.DataFrame, series: dict, bench: dict) -> pl.DataFrame:
    clean, _ = r46._deoverlap(events, sorted({d for item in series.values() for d in item[0]}))
    frame, _ = r46._add_car(clean, series, bench)
    return frame


def _control_cars(pairs: pl.DataFrame, series: dict, bench: dict) -> pl.DataFrame:
    if pairs.is_empty():
        return pl.DataFrame()
    rows = pairs.select([
        pl.col("pair_id"), pl.col("event_date"),
        pl.col("control_symbol").alias("symbol"),
    ]).with_columns(pl.col("event_date").alias("effective_date"))
    frame, _ = r46._add_car(rows, series, bench)
    return (
        frame.group_by("pair_id")
        .agg([
            pl.col("car5").mean().alias("control_car5"),
            pl.col("car20").mean().alias("control_car20"),
            pl.col("car60").mean().alias("control_car60"),
            pl.len().alias("control_count"),
        ])
    )


def _matched_did(events: pl.DataFrame, series: dict, bench: dict, features: pl.DataFrame, trading_dates: list[date], panel: pl.DataFrame, snapshots: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    evcars = _event_cars(events, series, bench)
    pairs, skipped = _event_features(events, features, trading_dates, panel, snapshots)
    controls = _control_cars(pairs, series, bench)
    if controls.is_empty():
        return pl.DataFrame(), {**skipped, "matched_events": 0, "avg_controls": 0}
    evcars = evcars.with_columns((pl.col("symbol") + "|" + pl.col("effective_date").cast(pl.Utf8)).alias("pair_id"))
    out = evcars.join(controls, on="pair_id", how="inner").with_columns([
        (pl.col("car5") - pl.col("control_car5")).alias("did5"),
        (pl.col("car20") - pl.col("control_car20")).alias("did20"),
        (pl.col("car60") - pl.col("control_car60")).alias("did60"),
    ])
    return out, {
        **skipped,
        "matched_events": out.height,
        "avg_controls": round(float(out["control_count"].mean()), 2) if out.height else 0,
        "did20_coverage": round(float(out["did20"].is_not_null().mean()), 4) if out.height else 0,
        "pairs": pairs.height,
    }


def _cluster_stats(frame: pl.DataFrame, col: str) -> dict:
    d = frame.filter(pl.col(col).is_not_null())
    if d.is_empty():
        return {"n": 0, "mean": None, "median": None, "t": None, "clusters": 0}
    g = d.group_by("effective_date").agg(pl.col(col).mean().alias("r")).sort("effective_date")
    vals = g["r"].to_list()
    return {**r46._stats(vals), "clusters": g.height}


def _trimmed(values: list[float]) -> float | None:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    lo = vals[min(len(vals) - 1, int(0.05 * (len(vals) - 1)))]
    hi = vals[min(len(vals) - 1, int(0.95 * (len(vals) - 1)))]
    out = [v for v in vals if lo <= v <= hi]
    return st.median(out) if out else None


def _portfolio_series(reb: pl.DataFrame, excluded: str) -> pl.DataFrame:
    dates = sorted(reb["date"].unique().to_list())
    prev = {}
    rows = []
    for day in dates:
        d = reb.filter(pl.col("date") == day)
        if excluded:
            d = d.filter(~pl.col(excluded))
        d = d.filter(pl.col("fwd").is_not_null())
        if d.is_empty():
            continue
        symbols = d["symbol"].to_list()
        weights = {s: 1.0 / len(symbols) for s in symbols}
        if prev:
            turn = 0.5 * sum(abs(weights.get(s, 0.0) - prev.get(s, 0.0)) for s in set(weights) | set(prev))
        else:
            turn = 1.0
        gross = float(d["fwd"].mean())
        rows.append({"date": day, "ym": day.strftime("%Y-%m"), "gross": gross, "turnover": turn, "r": gross - COST_2X * turn})
        prev = weights
    return pl.DataFrame(rows).sort("date") if rows else pl.DataFrame(schema={"date": pl.Date, "ym": pl.Utf8, "gross": pl.Float64, "turnover": pl.Float64, "r": pl.Float64})


def _overlay(reb: pl.DataFrame, events: pl.DataFrame, trading_dates: list[date]) -> tuple[dict, pl.DataFrame, pl.DataFrame]:
    event_idx = {}
    for keys, group in events.group_by("symbol"):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        event_idx[symbol] = sorted(bisect.bisect_left(trading_dates, d) for d in group["effective_date"].to_list())
    excluded = []
    for row in reb.iter_rows(named=True):
        idx = bisect.bisect_left(trading_dates, row["date"])
        last = max((i for i in event_idx.get(row["symbol"], []) if i <= idx), default=-10**9)
        excluded.append(idx - last < EXCLUDE_DAYS)
    reb = reb.with_columns(pl.Series("excluded", excluded))
    base = _portfolio_series(reb, "")
    over = _portfolio_series(reb, "excluded")
    phase_dates = r40.r33.phase_dates(r40.PHASE_TARGETS)
    base_frames, over_frames = [], []
    for phase in phase_dates:
        start = phase.strftime("%Y-%m")
        base_frames.append(base.filter(pl.col("ym") >= start))
        over_frames.append(over.filter(pl.col("ym") >= start))
    base_metrics = r30.phase_metrics(base_frames)
    over_metrics = r30.phase_metrics(over_frames)
    base_roll = r30.rolling_windows(base_frames)
    over_roll = r30.rolling_windows(over_frames)
    per_month = reb.filter(pl.col("fwd").is_not_null()).group_by("date").agg([
        pl.col("excluded").sum().alias("excluded"),
        pl.len().alias("eligible"),
    ]).sort("date")
    d2 = {
        "months_with_exclusion": int((per_month["excluded"] > 0).sum()),
        "avg_excluded": round(float(per_month["excluded"].mean()), 2),
        "max_excluded": int(per_month["excluded"].max()),
        "months": per_month.height,
    }
    decision = {
        "D2": bool(d2["months_with_exclusion"] >= 24 and d2["avg_excluded"] >= 2 and d2["max_excluded"] <= 50),
        "G1": bool(over_metrics["sharpe_mean"] >= base_metrics["sharpe_mean"] + 0.10),
        "G2": bool(over_metrics["annual_mean"] >= base_metrics["annual_mean"]),
        "G3": bool(over_metrics["mdd_mean"] >= base_metrics["mdd_mean"] - 0.01),
        "G4": bool(over_metrics["last_year_min"] >= base_metrics["last_year_min"] and over_roll["positive_rate"] >= base_roll["positive_rate"]),
    }
    decision["all_pass"] = all(decision.values())
    return {
        "base": {"metrics": base_metrics, "rolling": base_roll},
        "overlay": {"metrics": over_metrics, "rolling": over_roll},
        "decision": decision,
        "exclusion": d2,
    }, base, over


def main() -> int:
    events, event_audit = r46._events()
    memberships, _ = r40.membership_frame()
    symbols = sorted(memberships["symbol"].unique().to_list())
    combined, _ = r40.load_combined_prices(symbols)
    series, bench, trading_dates = r43._series_and_bench(combined, memberships)
    panel, reb, _ = r42.build_panel()
    reb = r42._scores(reb)
    snapshots = pl.read_parquet(r40.MEMBERSHIP_CACHE).with_columns([
        pl.col("query_date").cast(pl.Date),
        pl.col("symbol").cast(pl.Utf8),
    ])
    features = _panel_features(panel)
    did, match_audit = _matched_did(events, series, bench, features, trading_dates, panel, snapshots)
    if did.is_empty():
        stats = {"20": {"n": 0}, "60": {"n": 0}}
    else:
        stats = {"20": r46._stats(did["did20"].to_list()), "60": r46._stats(did["did60"].to_list())}
    clusters = {h: (_cluster_stats(did, f"did{h}") if not did.is_empty() else {"n": 0}) for h in (20, 60)}
    halves = {}
    for label, lo, hi in (("2021_2023", 2021, 2023), ("2024_2026", 2024, 2026)):
        d = did.filter((pl.col("effective_date").dt.year() >= lo) & (pl.col("effective_date").dt.year() <= hi))
        halves[label] = r46._stats(d["did20"].to_list()) if not d.is_empty() else {"n": 0}
    trimmed20 = _trimmed(did["did20"].to_list()) if not did.is_empty() else None
    d1 = bool(
        match_audit.get("matched_events", 0) >= 80
        and event_audit["effective_dates"] >= 10
        and match_audit.get("avg_controls", 0) >= MIN_CONTROLS
        and match_audit.get("did20_coverage", 0) >= 0.90
    )
    e1 = bool(stats["20"].get("median") is not None and stats["20"]["median"] < 0 and stats["20"].get("t") is not None and stats["20"]["t"] < -2)
    e2 = bool(stats["60"].get("median") is not None and stats["60"]["median"] < 0 and stats["60"].get("t") is not None and stats["60"]["t"] < -1.5)
    e3 = bool(all(v.get("median") is not None and v["median"] < 0 for v in halves.values()))
    e4 = bool(clusters[20].get("median") is not None and clusters[20]["median"] < 0 and clusters[20].get("t") is not None and clusters[20]["t"] < -1.5 and trimmed20 is not None and trimmed20 < 0)
    overlay, _base_series, _over_series = _overlay(reb, events, trading_dates)
    a_pass = bool(d1 and e1 and e2 and e3 and e4)
    all_pass = bool(a_pass and overlay["decision"]["all_pass"])
    result = {
        "post_hoc": True,
        "window": {"events": [str(events["effective_date"].min()), str(events["effective_date"].max())], "exclude_days": EXCLUDE_DAYS, "match_count": MATCH_COUNT, "min_controls": MIN_CONTROLS},
        "event_audit": event_audit,
        "matched": {"audit": match_audit, "stats": stats, "clusters": clusters, "halves": halves, "trimmed_median_did20": round(trimmed20, 5) if trimmed20 is not None else None},
        "overlay": overlay,
        "gates": {"D1": d1, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "A_pass": a_pass, **overlay["decision"]},
        "all_pass": all_pass,
        "recommendation": "R50 index-addition risk filter integration" if all_pass else "negative knowledge; do not deploy post-hoc reversal",
        "caveats": [
            "本轮方向来自 R46, 是事后假设, 不是独立样本外发现",
            "匹配使用当前 float_mv 和当前行业快照; float_mv 非历史 PIT",
            "纳入识别依赖月度 BaoStock 快照, 事件日使用 official effective_date",
            "叠加层只做 long-only 等权, 不证明对 B80/C6_IND 有增量",
        ],
    }
    path = EV / "r49_index_overlay.json"
    path.write_text(json.dumps(r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("matched", match_audit, "stats", stats, "clusters", clusters, "halves", halves, flush=True)
    print("overlay", overlay["decision"], overlay["exclusion"], flush=True)
    print("gates", result["gates"], "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
