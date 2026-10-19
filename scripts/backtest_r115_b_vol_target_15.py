#!/usr/bin/env python3
"""R115: tighten the B-sleeve volatility target from 20% to 15%.

This is a follow-up to R114, not an independent confirmation.  Everything except
the target volatility is unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R114_JSON = EVIDENCE / "r114_b_vol_target.json"
OUT = EVIDENCE / "r115_b_vol_target_15.json"


def _load_r114():
    spec = importlib.util.spec_from_file_location("r114", R114_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.TARGET_VOL = 0.15
    return module


def main() -> int:
    r114 = _load_r114()
    r112 = r114._load_r112()
    membership = r112._historical_membership()
    service = r112._service()

    phase_outputs = {}
    recent_outputs = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        b = r112._run_sleeve(
            service,
            membership,
            strategy_id=r112.SLEEVES["B_industry_relative_strength_h20_sl20"],
            start=phase,
            end=r112.END,
        )
        overlay = r114._vol_target(b)
        rec = {
            "phase": key,
            "B_industry_relative_strength_h20_sl20": r112._stats(b),
            "B_vol_target_15pct": r112._stats(overlay["curve"]),
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
            f"[{key}] B={rec['B_industry_relative_strength_h20_sl20']['total_return']:+.3f} "
            f"vol15={rec['B_vol_target_15pct']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = (
        "B_industry_relative_strength_h20_sl20",
        "B_vol_target_15pct",
    )
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
    weight_mean = round(
        st.mean(phase_outputs[key]["weight_mean"] for key in phase_outputs), 4
    )
    weight_min = round(
        min(phase_outputs[key]["weight_min"] for key in phase_outputs), 4
    )
    weight_max = round(
        max(phase_outputs[key]["weight_max"] for key in phase_outputs), 4
    )

    r114_result = json.loads(R114_JSON.read_text(encoding="utf-8"))
    r114_long = r114_result["long"]["B_vol_target_20pct"]
    overlay = long["B_vol_target_15pct"]
    base = long["B_industry_relative_strength_h20_sl20"]

    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    e1 = bool(
        overlay["annual_mean"] >= 0.15
        and overlay["sharpe_mean"] >= 0.90
        and overlay["mdd_mean"] >= -0.225
        and overlay["mdd_worst"] >= -0.30
    )
    e2 = overlay["positive_phases"] == 10
    e3 = bool(
        recent["B_vol_target_15pct"]["total_return_mean"] > 0
        and recent["B_vol_target_15pct"]["positive_phases"] >= 4
    )
    e4 = bool(
        overlay["sharpe_mean"] >= base["sharpe_mean"] + 0.15
        and overlay["annual_mean"] >= base["annual_mean"] * 0.60
        and overlay["mdd_mean"] >= base["mdd_mean"] + 0.15
    )
    e5 = bool(0.30 <= weight_mean <= 1.00)
    e6 = annual_turnover_mean <= 1.5
    e7 = bool(
        overlay["mdd_worst"] >= r114_long["mdd_worst"] + 0.05
        and overlay["sharpe_mean"] >= r114_long["sharpe_mean"] - 0.03
    )
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
        "E5": e5,
        "E6": e6,
        "E7": e7,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R115 15% annual volatility target on the historical B sleeve",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeve": r112.SLEEVES["B_industry_relative_strength_h20_sl20"],
        "rule": {
            "lookback_days": 60,
            "target_annual_volatility": 0.15,
            "weight_bounds": [r114.MIN_WEIGHT, r114.MAX_WEIGHT],
            "rebalance": "monthly",
            "round_trip_cost": r114.ROUND_TRIP_COST,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "weight": {
            "mean": weight_mean,
            "min": weight_min,
            "max": weight_max,
        },
        "r114_comparison": {
            "vol_target_20": {
                "sharpe": r114_long["sharpe_mean"],
                "annual": r114_long["annual_mean"],
                "mdd_mean": r114_long["mdd_mean"],
                "mdd_worst": r114_long["mdd_worst"],
            },
            "delta": {
                "sharpe": round(overlay["sharpe_mean"] - r114_long["sharpe_mean"], 4),
                "annual": round(overlay["annual_mean"] - r114_long["annual_mean"], 4),
                "mdd_mean": round(overlay["mdd_mean"] - r114_long["mdd_mean"], 4),
                "mdd_worst": round(overlay["mdd_worst"] - r114_long["mdd_worst"], 4),
            },
        },
        "decision": decision,
        "final_recommendation": (
            "R115 B 15% volatility target candidate for forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "This is a follow-up informed by R114, not an independent confirmation.",
            "The industry sleeve still uses current industry snapshots backfilled over history.",
            "The 3x cost convention is a stress test, not an execution-cost estimate.",
            "No production approval is implied by this evidence.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long overlay", long["B_vol_target_15pct"], flush=True)
    print("recent overlay", recent["B_vol_target_15pct"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())