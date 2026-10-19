#!/usr/bin/env python3
"""R120: 12.5% volatility target under the PIT Shenwan industry sleeve.

Reuses the R119 B-sleeve caches (PIT industry) and only changes the target
volatility from 15% to 12.5%.  No underlying strategy is re-run.
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
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R119_JSON = EVIDENCE / "r119_pit_industry_vol_target.json"
OUT = EVIDENCE / "r120_pit_industry_vol_target_125.json"
R119_CACHE = DATA / "research" / "r119_cache"
TARGET_VOL = 0.125


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = TARGET_VOL
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]

    phase_outputs = {}
    recent_outputs = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R119_CACHE / f"{strategy_id}_{phase.isoformat()}_{r112.END.isoformat()}.parquet"
        if not path.exists():
            raise RuntimeError(f"missing R119 cache: {path}")
        b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        overlay = r114._vol_target(b)
        rec = {
            "phase": key,
            "B_pit_industry": r112._stats(b),
            "B_pit_industry_vol_target_125pct": r112._stats(overlay["curve"]),
            "annual_turnover": round(overlay["annual_turnover"], 4),
            "weight_mean": round(st.mean(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_min": round(min(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_max": round(max(row["weight"] for row in overlay["weight_records"]), 4),
        }
        if phase in r112.PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec
        print(
            f"[{key}] B={rec['B_pit_industry']['total_return']:+.3f} "
            f"vol12.5={rec['B_pit_industry_vol_target_125pct']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = ("B_pit_industry", "B_pit_industry_vol_target_125pct")
    long = {
        name: r112._aggregate([phase_outputs[key][name] for key in sorted(phase_outputs)])
        for name in names
    }
    recent = {
        name: r112._aggregate([recent_outputs[key][name] for key in sorted(recent_outputs)])
        for name in names
    }
    annual_turnover_mean = round(
        st.mean(phase_outputs[key]["annual_turnover"] for key in phase_outputs), 4
    )
    weight_mean = round(st.mean(phase_outputs[key]["weight_mean"] for key in phase_outputs), 4)
    weight_min = round(min(phase_outputs[key]["weight_min"] for key in phase_outputs), 4)
    weight_max = round(max(phase_outputs[key]["weight_max"] for key in phase_outputs), 4)

    r119 = json.loads(R119_JSON.read_text(encoding="utf-8"))
    r119_long = r119["long"]["B_pit_industry_vol_target_15pct"]
    overlay = long["B_pit_industry_vol_target_125pct"]
    base = long["B_pit_industry"]

    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    e1 = bool(
        overlay["annual_mean"] >= 0.15
        and overlay["sharpe_mean"] >= 0.90
        and overlay["mdd_mean"] >= -0.225
        and overlay["mdd_worst"] >= -0.30
    )
    e2 = overlay["positive_phases"] == 10
    e3 = bool(
        recent["B_pit_industry_vol_target_125pct"]["total_return_mean"] > 0
        and recent["B_pit_industry_vol_target_125pct"]["positive_phases"] >= 4
    )
    e4 = bool(
        overlay["sharpe_mean"] >= base["sharpe_mean"] + 0.15
        and overlay["annual_mean"] >= base["annual_mean"] * 0.60
        and overlay["mdd_mean"] >= base["mdd_mean"] + 0.15
    )
    e5 = bool(0.30 <= weight_mean <= 1.00)
    e6 = annual_turnover_mean <= 1.5
    e7 = bool(
        overlay["mdd_worst"] >= r119_long["mdd_worst"] + 0.03
        and overlay["sharpe_mean"] >= r119_long["sharpe_mean"] - 0.05
    )
    decision = {"D1": d1, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6, "E7": e7}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R120 12.5% volatility target under the PIT Shenwan industry sleeve",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeve": strategy_id,
        "industry": r119.get("industry"),
        "rule": {
            "lookback_days": 60,
            "target_annual_volatility": TARGET_VOL,
            "weight_bounds": [0.2, 1.0],
            "rebalance": "monthly",
            "round_trip_cost": 0.0052,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "weight": {"mean": weight_mean, "min": weight_min, "max": weight_max},
        "r119_comparison": {
            "vol_target_15": {
                "sharpe": r119_long["sharpe_mean"],
                "annual": r119_long["annual_mean"],
                "mdd_mean": r119_long["mdd_mean"],
                "mdd_worst": r119_long["mdd_worst"],
            },
            "delta": {
                "sharpe": round(overlay["sharpe_mean"] - r119_long["sharpe_mean"], 4),
                "annual": round(overlay["annual_mean"] - r119_long["annual_mean"], 4),
                "mdd_mean": round(overlay["mdd_mean"] - r119_long["mdd_mean"], 4),
                "mdd_worst": round(overlay["mdd_worst"] - r119_long["mdd_worst"], 4),
            },
        },
        "decision": decision,
        "final_recommendation": (
            "12.5% volatility target is robust under PIT industry"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Reuses R119 PIT-industry B-sleeve caches; no underlying strategy re-run.",
            "SW industry code first 2 digits is a proxy for L1, not official name.",
            "The 3x cost convention is a stress test, not an execution-cost estimate.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long overlay", long["B_pit_industry_vol_target_125pct"], flush=True)
    print("recent overlay", recent["B_pit_industry_vol_target_125pct"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())