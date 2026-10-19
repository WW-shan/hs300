#!/usr/bin/env python3
"""R124: real rolling 1-year windows for the PIT-industry volatility candidate.

For every R119 phase cache, re-apply the 15% volatility target on the B-sleeve
curve, then compute all overlapping 252-trading-day windows.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
OUT = EVIDENCE / "r124_rolling_1y_pit.json"
R119_CACHE = DATA / "research" / "r119_cache"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for i in range(WINDOW, len(values)):
        start = values[i - WINDOW]
        end = values[i]
        window = values[i - WINDOW:i + 1]
        peak = window[0]
        mdd = 0.0
        for v in window:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        out.append({"return": end / start - 1.0, "mdd": mdd})
    if not out and len(values) >= 60:
        # Short fresh-start phase: treat the whole phase as one window.
        window = values
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
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]

    base_records = []
    cand_records = []
    per_phase = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R119_CACHE / f"{strategy_id}_{key}_{r112.END.isoformat()}.parquet"
        if not path.exists():
            raise RuntimeError(f"missing R119 cache: {path}")
        b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        overlay = r114._vol_target(b)
        base_windows = _window_stats(b["value"].to_list())
        cand_windows = _window_stats(overlay["curve"]["value"].to_list())
        base_records.extend(base_windows)
        cand_records.extend(cand_windows)
        per_phase[key] = {
            "base": _summarize(base_windows),
            "candidate": _summarize(cand_windows),
        }
        print(
            f"[{key}] base_pos={per_phase[key]['base']['positive_rate']} "
            f"cand_pos={per_phase[key]['candidate']['positive_rate']} "
            f"cand_median={per_phase[key]['candidate']['return_median']:+.3f}",
            flush=True,
        )

    base = _summarize(base_records)
    cand = _summarize(cand_records)

    d1 = len(per_phase) == 15 and cand["windows"] >= 300
    g1 = cand["positive_rate"] >= 0.85
    g2 = cand["return_p05"] >= -0.15
    g3 = cand["return_median"] >= 0.05
    g4 = cand["return_worst"] >= -0.25
    g5 = cand["positive_rate"] >= base["positive_rate"] + 0.05
    g6 = cand["mdd_p05"] >= -0.35
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R124 real rolling 1-year windows for the PIT-industry candidate",
        "universe": "R119 PIT Shenwan industry B-sleeve caches",
        "method": {"window_days": WINDOW, "overlapping": True, "phases": len(per_phase)},
        "baseline_B_pit_industry": base,
        "candidate_vol_target_15pct": cand,
        "per_phase": per_phase,
        "decision": decision,
        "final_recommendation": (
            "real rolling 1-year distribution supports forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Windows overlap heavily; this is descriptive, not a significance test.",
            "Phases share history, so windows are not independent.",
            "The overlay was calibrated on this sample; calibration bias remains.",
            "SW industry code first 2 digits is a proxy for L1, not official name.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("baseline", base, flush=True)
    print("candidate", cand, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())