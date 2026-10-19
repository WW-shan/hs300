#!/usr/bin/env python3
"""R131: honest dual-sleeve candidate with PIT Shenwan industry B sleeve.

A sleeve: R125 fixed macro-gate cache (3x cost).
B sleeve: R119 PIT-industry cache (3x cost).
Portfolio and overlay identical to R126.
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
OUT = EVIDENCE / "r131_pit_dual_sleeve.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
WINDOW = 252
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"


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
        "return_p10": round(_quantile(returns, 0.10), 4),
        "return_median": round(st.median(returns), 4),
        "return_worst": round(min(returns), 4),
        "return_best": round(max(returns), 4),
        "mdd_p05": round(_quantile(mdds, 0.05), 4),
        "mdd_median": round(st.median(mdds), 4),
        "mdd_worst": round(min(mdds), 4),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    base_records = []
    cand_records = []
    per_phase = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        overlay = r114._vol_target(risk)
        base_windows = _window_stats(risk["value"].to_list())
        cand_windows = _window_stats(overlay["curve"]["value"].to_list())
        base_records.extend(base_windows)
        cand_records.extend(cand_windows)
        per_phase[key] = {
            "base_pit_dual_risk_parity": _summarize(base_windows),
            "candidate_pit_dual_vol15": _summarize(cand_windows),
        }
        if cand_windows:
            print(
                f"[{key}] base_pos={per_phase[key]['base_pit_dual_risk_parity']['positive_rate']} "
                f"cand_pos={per_phase[key]['candidate_pit_dual_vol15']['positive_rate']} "
                f"cand_median={per_phase[key]['candidate_pit_dual_vol15']['return_median']:+.3f}",
                flush=True,
            )

    base = _summarize(base_records)
    cand = _summarize(cand_records)
    r126 = json.loads(
        (EVIDENCE / "r126_dual_sleeve_vol_target_rolling.json").read_text(encoding="utf-8")
    )

    d1 = len(per_phase) == 15 and cand["windows"] >= 300
    g1 = cand["positive_rate"] >= 0.85
    g2 = cand["return_worst"] >= -0.25
    g3 = cand["return_p05"] >= -0.15
    g4 = cand["positive_rate"] >= r126["candidate_vol_target_15"]["positive_rate"] - 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R131 honest dual-sleeve candidate with PIT industry B sleeve",
        "universe": "R125 A cache + R119 PIT-industry B cache, 3x cost",
        "method": {
            "portfolio": "60d inverse-vol risk parity, monthly, bounds [0.2, 0.8]",
            "overlay": "60d realized vol target 15%, monthly, bounds [0.2, 1.0]",
            "window_days": WINDOW,
            "phases": len(per_phase),
        },
        "baseline_pit_dual_risk_parity": base,
        "candidate_pit_dual_vol15": cand,
        "reference_r126_current_snapshot": r126["candidate_vol_target_15"],
        "per_phase": per_phase,
        "decision": decision,
        "final_recommendation": (
            "PIT-industry dual-sleeve vol-target candidate for forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Windows overlap heavily; descriptive statistics only.",
            "PIT industry mapping covers 99.72% of rows; the remainder is excluded.",
            "The overlay parameters were calibrated on this sample.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("base", base, flush=True)
    print("candidate", cand, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
