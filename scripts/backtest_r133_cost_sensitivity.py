#!/usr/bin/env python3
"""R133: execution-cost sensitivity and turnover for the PIT honest candidate.

The stock layer already carries 3x cost inside the cached sleeve curves.
This script varies the portfolio-layer round-trip cost (risk-parity
rebalance + vol-target resize) and reports turnover.
"""
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
OUT = EVIDENCE / "r133_cost_sensitivity.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
WINDOW = 252
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
COSTS = [0.0, 0.0026, 0.0052, 0.010, 0.020]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        window = values[start : start + WINDOW]
        peak = window[0]
        mdd = 0.0
        for v in window:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        out.append({"return": window[-1] / window[0] - 1.0, "mdd": mdd})
    return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[index]


def _summarize(records: list[dict]) -> dict:
    if not records:
        return {"windows": 0}
    returns = [r["return"] for r in records]
    mdds = [r["mdd"] for r in records]
    return {
        "windows": len(records),
        "positive_rate": round(sum(v > 0 for v in returns) / len(returns), 4),
        "return_p05": round(_quantile(returns, 0.05), 4),
        "return_median": round(st.median(returns), 4),
        "return_worst": round(min(returns), 4),
        "mdd_p05": round(_quantile(mdds, 0.05), 4),
        "mdd_worst": round(min(mdds), 4),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    curves = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        curves[key] = (a, b)

    results = {}
    turnover_records = []
    for cost in COSTS:
        records = []
        for key, (a, b) in curves.items():
            r112.ROUND_TRIP_COST = cost
            r114.ROUND_TRIP_COST = cost
            risk, _ = r112._combine(a, b, "risk")
            overlay = r114._vol_target(risk)
            records.extend(_window_stats(overlay["curve"]["value"].to_list()))
            if cost == 0.0052:
                turnover_records.append(overlay["annual_turnover"])
        label = f"cost={cost}"
        results[label] = _summarize(records)
        print(f"{label}: {json.dumps(results[label], ensure_ascii=False)}", flush=True)

    # risk-parity layer turnover: approximate from weight changes
    r112.ROUND_TRIP_COST = 0.0052
    r114.ROUND_TRIP_COST = 0.0052
    rp_turnovers = []
    for key, (a, b) in curves.items():
        joined = (
            a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
        )
        dates = joined["date"].to_list()
        va = joined["a"].to_list()
        vb = joined["b"].to_list()
        ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
        rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
        prev_month = None
        prev_w = 0.5
        tot = 0.0
        for i, day in enumerate(dates):
            month = (day.year, day.month)
            if i == 0 or month != prev_month:
                if i < 60:
                    w = 0.5
                else:
                    wa = [0.0, *ra][i - 60 : i]
                    wb = [0.0, *rb][i - 60 : i]
                    sa = max(st.pstdev(wa), 1e-6)
                    sb = max(st.pstdev(wb), 1e-6)
                    w = (1.0 / sa) / (1.0 / sa + 1.0 / sb)
                    w = min(0.8, max(0.2, w))
                tot += abs(w - prev_w)
                prev_w = w
                prev_month = month
        rp_turnovers.append(tot * 252.0 / max(len(dates) - 1, 1))

    base = results["cost=0.0052"]
    d1 = abs(base["positive_rate"] - 0.8604) <= 0.001
    g1 = results["cost=0.01"]["positive_rate"] >= 0.85
    g2 = results["cost=0.02"]["positive_rate"] >= 0.83
    g3 = results["cost=0.02"]["return_worst"] >= -0.20
    turnover_mean = st.mean(turnover_records)
    rp_mean = st.mean(rp_turnovers)
    g4 = (turnover_mean + rp_mean) <= 3.0
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R133 execution-cost sensitivity and turnover for the PIT candidate",
        "stock_layer_cost": "3x convention (6bp commission + 15bp slippage + 10bp stamp)",
        "portfolio_layer_costs": COSTS,
        "results": results,
        "turnover": {
            "overlay_annual_mean": round(turnover_mean, 4),
            "risk_parity_annual_mean": round(rp_mean, 4),
            "combined_annual_mean": round(turnover_mean + rp_mean, 4),
            "per_phase_overlay": [round(x, 4) for x in turnover_records],
        },
        "decision": decision,
        "caveats": [
            "Overlapping windows; descriptive only.",
            "Portfolio-layer cost is charged per unit turnover at monthly rebalances.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("turnover:", json.dumps(result["turnover"], ensure_ascii=False), flush=True)
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
