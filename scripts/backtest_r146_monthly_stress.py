#!/usr/bin/env python3
"""R146: monthly performance and stress-segment decomposition."""
from __future__ import annotations

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
OUT = EVIDENCE / "r146_monthly_stress.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
PHASE = date(2021, 6, 1)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    key = PHASE.isoformat()
    a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
    b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
    risk, _ = r112._combine(a, b, "risk")
    cand = r114._vol_target(risk)["curve"].sort("date")

    # monthly returns
    cand = cand.with_columns(pl.col("date").dt.strftime("%Y-%m").alias("ym"))
    monthly = (
        cand.group_by("ym").agg([
            pl.col("value").first().alias("first"),
            pl.col("value").last().alias("last"),
            pl.col("date").min().alias("start"),
            pl.col("date").max().alias("end"),
        ]).sort("ym")
    )
    monthly = monthly.with_columns((pl.col("last") / pl.col("first") - 1.0).alias("ret"))
    rets = monthly["ret"].to_list()
    win_rate = sum(1 for r in rets if r > 0) / len(rets)

    # longest losing streak
    streak = best = 0
    for r in rets:
        if r <= 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0

    # index for stress segments
    idx = (
        pl.scan_parquet([str(p) for p in sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    joined = cand.join(idx, on="date", how="inner").sort("date")

    # find index drawdown segments > 10%: peak-to-trough
    values = joined["close"].to_list()
    dates = joined["date"].to_list()
    cand_vals = joined["value"].to_list()
    segments = []
    peak_i = 0
    trough_i = 0
    for i in range(1, len(values)):
        if values[i] > values[peak_i]:
            # close out any completed segment
            if trough_i > peak_i and values[trough_i] / values[peak_i] - 1.0 <= -0.10:
                segments.append((peak_i, trough_i))
            peak_i = i
            trough_i = i
        elif values[i] < values[trough_i]:
            trough_i = i
    if trough_i > peak_i and values[trough_i] / values[peak_i] - 1.0 <= -0.10:
        segments.append((peak_i, trough_i))

    seg_records = []
    for p_i, t_i in segments:
        seg_records.append({
            "start": str(dates[p_i]),
            "end": str(dates[t_i]),
            "index_return": round(values[t_i] / values[p_i] - 1.0, 4),
            "candidate_return": round(cand_vals[t_i] / cand_vals[p_i] - 1.0, 4),
        })

    # candidate max drawdown and recovery
    peak = cand_vals[0]
    peak_i = 0
    mdd = 0.0
    mdd_peak_i = mdd_trough_i = 0
    for i, v in enumerate(cand_vals):
        if v > peak:
            peak = v
            peak_i = i
        dd = v / peak - 1.0
        if dd < mdd:
            mdd = dd
            mdd_peak_i = peak_i
            mdd_trough_i = i
    recovery_i = None
    for i in range(mdd_trough_i, len(cand_vals)):
        if cand_vals[i] >= cand_vals[mdd_peak_i]:
            recovery_i = i
            break
    recovery_months = None
    if recovery_i is not None:
        days = (dates[recovery_i] - dates[mdd_trough_i]).days
        recovery_months = round(days / 30.44, 1)

    d1 = len(rets) >= 60 and len(seg_records) >= 3
    g1 = win_rate >= 0.55
    g2 = best <= 6
    g3 = all(s["candidate_return"] >= s["index_return"] - 0.05 for s in seg_records)
    g4 = recovery_months is not None and recovery_months <= 12
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R146 monthly performance and stress segments",
        "months": len(rets),
        "monthly_win_rate": round(win_rate, 4),
        "monthly_mean": round(st.mean(rets), 5),
        "monthly_median": round(st.median(rets), 5),
        "monthly_best": round(max(rets), 4),
        "monthly_worst": round(min(rets), 4),
        "longest_losing_streak": best,
        "monthly_returns": [{"ym": m["ym"], "ret": round(m["ret"], 5)} for m in monthly.to_dicts()],
        "stress_segments": seg_records,
        "max_drawdown": round(mdd, 4),
        "mdd_peak_date": str(dates[mdd_peak_i]),
        "mdd_trough_date": str(dates[mdd_trough_i]),
        "recovery_months": recovery_months,
        "decision": decision,
        "caveats": ["Single phase; segments derived from index drawdowns."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("months:", len(rets), "win_rate:", round(win_rate, 4), "streak:", best, flush=True)
    print("stress segments:", json.dumps(seg_records, ensure_ascii=False), flush=True)
    print("mdd:", round(mdd, 4), "recovery_months:", recovery_months, flush=True)
    print("worst months:", sorted(result["monthly_returns"], key=lambda r: r["ret"])[:5], flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
