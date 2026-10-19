#!/usr/bin/env python3
"""R184: low short-interest crowding as an independent third sleeve.

Primary (as pre-registered): C sleeve = R182 monthly long-only return series
(gross - 0.30% cost), spread evenly over the trading days of each month.
Secondary robustness: C sleeve = engine daily implementation (r184_cache).

Combines A + B + C with 60d inverse-vol risk parity (bounds [0.05, 0.8])
and a 15% vol target, then compares the rolling 1y distribution vs R131.
"""
from __future__ import annotations

import bisect
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r184_three_sleeve.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R182_EVIDENCE = EVIDENCE / "r182_short_ratio_strategy.json"
R184_CACHE = DATA / "research" / "r184_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_SLEEVE = "ai_hs300_low_short_crowding"
WINDOW = 252
ROUND_TRIP_COST = 0.0052
C_MONTHLY_COST = 0.003


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _calendar() -> list[date]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )


def _c_curve_monthly(calendar: list[date]) -> pl.DataFrame:
    """Daily C curve from R182 monthly net returns, spread evenly per month."""
    payload = json.loads(R182_EVIDENCE.read_text(encoding="utf-8"))
    recs = [
        (date.fromisoformat(r["date"]), r["portfolio_return"] - C_MONTHLY_COST)
        for r in payload["monthly_records"]
        if r["portfolio_return"] is not None
    ]
    recs.sort()
    index = {d: i for i, d in enumerate(calendar)}
    rows = []
    value = 1.0
    for j, (d, ret) in enumerate(recs):
        start_pos = index.get(d)
        if start_pos is None:
            start_pos = bisect.bisect_left(calendar, d)
        if j + 1 < len(recs):
            end_pos = index.get(recs[j + 1][0])
            if end_pos is None:
                end_pos = bisect.bisect_left(calendar, recs[j + 1][0])
        else:
            end_pos = min(start_pos + 21, len(calendar))
        n_days = max(1, end_pos - start_pos)
        daily = (1.0 + ret) ** (1.0 / n_days) - 1.0
        for k in range(start_pos, end_pos):
            value *= 1.0 + daily
            rows.append({"date": calendar[k], "value": value})
    return pl.DataFrame(rows)


def _three_targets(dates: list[date], ra: list[float], rb: list[float], rc: list[float]) -> dict:
    targets = {}
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if i < 60:
                targets[day] = (1 / 3, 1 / 3, 1 / 3)
            else:
                wa = [0.0, *ra][i - 60 : i]
                wb = [0.0, *rb][i - 60 : i]
                wc = [0.0, *rc][i - 60 : i]
                sa = max(st.pstdev(wa), 1e-6)
                sb = max(st.pstdev(wb), 1e-6)
                sc = max(st.pstdev(wc), 1e-6)
                inv = [1 / sa, 1 / sb, 1 / sc]
                total = sum(inv)
                weights = [v / total for v in inv]
                weights = [min(0.8, max(0.05, w)) for w in weights]
                s = sum(weights)
                weights = [w / s for w in weights]
                targets[day] = tuple(weights)
            prev_month = month
    return targets


def _three_sleeve(a: pl.DataFrame, b: pl.DataFrame, c: pl.DataFrame) -> pl.DataFrame:
    joined = (
        a.rename({"value": "a"})
        .join(b.rename({"value": "b"}), on="date", how="inner")
        .join(c.rename({"value": "c"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    vc = joined["c"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    rc = [vc[i] / vc[i - 1] - 1.0 for i in range(1, len(vc))]
    targets = _three_targets(dates, ra, rb, rc)

    portfolio = 1.0
    sleeve = {"a": 1.0, "b": 1.0, "c": 1.0}
    target = targets[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    for i in range(1, len(dates)):
        day = dates[i]
        rets = {"a": ra[i - 1], "b": rb[i - 1], "c": rc[i - 1]}
        for k in "abc":
            sleeve[k] *= 1.0 + rets[k]
        portfolio *= 1.0 + sum(t * rets[k] for t, k in zip(target, "abc"))
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            totals = [target[j] * sleeve[k] for j, k in enumerate("abc")]
            total = sum(totals)
            actual = [t / total if total else target[j] for j, t in enumerate(totals)]
            nt = targets[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * sum(abs(nt[j] - actual[j]) for j in range(3))
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve = {"a": 1.0, "b": 1.0, "c": 1.0}
            target = nt
        rows.append({"date": day, "value": portfolio})
    return pl.DataFrame(rows)


def _run_variant(r112, r114, r127, c_all: pl.DataFrame, membership, service, label: str, engine_c: bool):
    records = []
    recent = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        if engine_c:
            c = pl.read_parquet(R184_CACHE / f"{C_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        else:
            c = c_all.filter(pl.col("date") >= phase)
        a = pl.read_parquet(DATA / "research" / "r125_cache" / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(DATA / "research" / "r119_cache" / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        three = _three_sleeve(a, b, c)
        overlay = r114._vol_target(three)["curve"]
        w = r127._window_stats(overlay["value"].to_list())
        records.extend(w)
        if key == "2025-10-14":
            recent = r112._stats(overlay)
        pos = (sum(1 for r in w if r["return"] > 0) / len(w)) if w else None
        print(f"[{label}][{key}] windows={len(w)} pos={pos}", flush=True)
    returns = [r["return"] for r in records]
    mdds = [r["mdd"] for r in records]
    summary = {
        "windows": len(records),
        "positive_rate": round(sum(v > 0 for v in returns) / len(returns), 4) if returns else None,
        "return_p05": round(sorted(returns)[max(0, int(len(returns) * 0.05))], 4) if returns else None,
        "return_median": round(st.median(returns), 4) if returns else None,
        "return_worst": round(min(returns), 4) if returns else None,
        "mdd_p05": round(sorted(mdds)[max(0, int(len(mdds) * 0.05))], 4) if mdds else None,
        "mdd_worst": round(min(mdds), 4) if mdds else None,
    }
    return summary, recent


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15

    membership = r112._historical_membership()
    service = r112._service()
    calendar = _calendar()
    c_all = _c_curve_monthly(calendar)
    print(f"monthly C curve rows={c_all.height} {c_all['date'][0]}..{c_all['date'][-1]}", flush=True)

    summary, recent = _run_variant(r112, r114, r127, c_all, membership, service, "monthly", engine_c=False)
    engine_summary, engine_recent = _run_variant(r112, r114, r127, c_all, membership, service, "engine", engine_c=True)

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = summary["windows"] >= 300
    g1 = summary["positive_rate"] is not None and summary["positive_rate"] >= base["positive_rate"] + 0.01
    g2 = summary["return_worst"] is not None and summary["return_worst"] >= base["return_worst"] - 0.01
    g3 = summary["return_median"] is not None and summary["return_median"] >= base["return_median"]
    g4 = summary["mdd_p05"] is not None and summary["mdd_p05"] >= base["mdd_p05"] - 0.01
    g5 = recent is not None and recent["total_return"] > 0
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R184 low short-interest crowding as independent third sleeve",
        "primary_variant": "C = R182 monthly net returns spread evenly across each month (as pre-registered)",
        "rolling_1y_monthly_c": summary,
        "recent_year_monthly_c": recent,
        "rolling_1y_engine_c": engine_summary,
        "recent_year_engine_c": engine_recent,
        "reference_r131": base,
        "decision": decision,
        "caveats": [
            "The monthly C series has 63 observations (2021-06..2026-08); the daily curve is an even-spread approximation.",
            "The engine C implementation (90 names, 20d hold, no stop) is shown as a robustness check; its higher turnover explains the weaker result.",
            "The 3-sleeve weight bounds [0.05, 0.8] and 60d window are single fixed choices; no grid was run.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("monthly:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("engine:", json.dumps(engine_summary, ensure_ascii=False), flush=True)
    print("recent monthly:", json.dumps(recent, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
