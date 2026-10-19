#!/usr/bin/env python3
"""R150: rerun the R113 B-momentum switch rule on the fixed A sleeve."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r150_switch_fixed.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
ROUND_TRIP_COST = 0.0052


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _combine_switch(a: pl.DataFrame, b: pl.DataFrame):
    joined = (
        a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
    )
    dates = joined["date"].to_list()
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    portfolio = 1.0
    sa = sb = 1.0
    target_a = 0.5
    rows = [{"date": dates[0], "value": portfolio}]
    total_turnover = 0.0
    for i in range(1, len(dates)):
        day = dates[i]
        sa *= 1.0 + ra[i - 1]
        sb *= 1.0 + rb[i - 1]
        portfolio *= 1.0 + target_a * ra[i - 1] + (1.0 - target_a) * rb[i - 1]
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target_a * sa + (1.0 - target_a) * sb
            actual_a = target_a * sa / total if total else target_a
            actual_b = 1.0 - actual_a
            if i + 1 < len(dates):
                if i < 60:
                    nt = 0.5
                else:
                    b_mom = vb[i] / vb[i - 60] - 1.0
                    nt = 0.2 if b_mom >= 0.0 else 0.8
            else:
                nt = target_a
            turnover = 0.5 * (abs(nt - actual_a) + abs((1.0 - nt) - actual_b))
            total_turnover += turnover
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sa = sb = 1.0
            target_a = nt
        rows.append({"date": day, "value": portfolio})
    annual_turnover = total_turnover * 252.0 / max(len(dates) - 1, 1)
    return pl.DataFrame(rows), annual_turnover


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15

    switch_records = []
    overlay_records = []
    fixed_records = []
    turnover_values = []
    recent = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        switch, turnover = _combine_switch(a, b)
        overlay = r114._vol_target(switch)["curve"]
        fixed, _ = r112._combine(a, b, "fixed")
        switch_records.extend(r127._window_stats(switch["value"].to_list()))
        overlay_records.extend(r127._window_stats(overlay["value"].to_list()))
        fixed_records.extend(r127._window_stats(fixed["value"].to_list()))
        turnover_values.append(turnover)
        if key == "2025-10-14":
            recent = r112._stats(overlay)
        print(f"[{key}] switch_pos={sum(1 for w in r127._window_stats(switch['value'].to_list()) if w['return']>0)/max(len(r127._window_stats(switch['value'].to_list())),1):.3f} turnover={turnover:.2f}", flush=True)

    switch_summary = r127._summarize(switch_records)
    overlay_summary = r127._summarize(overlay_records)
    fixed_summary = r127._summarize(fixed_records)
    turnover_mean = st.mean(turnover_values)

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = len(turnover_values) == 15
    g1 = overlay_summary["positive_rate"] >= base["positive_rate"]
    g2 = overlay_summary["return_worst"] >= -0.15
    g3 = recent is not None and recent["total_return"] > 0
    g4 = turnover_mean <= 2.0
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R150 B-momentum switch rerun on fixed data",
        "switch_only": switch_summary,
        "switch_plus_overlay": overlay_summary,
        "fixed_50_50": fixed_summary,
        "reference_r131": base,
        "annual_turnover_mean": round(turnover_mean, 4),
        "recent_year_overlay": recent,
        "decision": decision,
        "caveats": ["Windows overlap; descriptive only."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("switch:", json.dumps(switch_summary, ensure_ascii=False), flush=True)
    print("overlay:", json.dumps(overlay_summary, ensure_ascii=False), flush=True)
    print("turnover:", round(turnover_mean, 4), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
