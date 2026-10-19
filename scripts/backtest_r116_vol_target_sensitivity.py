#!/usr/bin/env python3
"""R116: sensitivity of the B-sleeve volatility target around 15%.

Targets: 12.5%, 15%, 17.5%.  The 15% version remains the candidate; this run
only checks whether it is a lonely point or part of a stable neighborhood.
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
OUT = EVIDENCE / "r116_vol_target_sensitivity.json"
TARGETS = (0.125, 0.15, 0.175)


def _load_r114():
    spec = importlib.util.spec_from_file_location("r114", R114_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _evaluate_target(r114, r112, membership, service, target: float):
    r114.TARGET_VOL = target
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
            "overlay": r112._stats(overlay["curve"]),
            "annual_turnover": round(overlay["annual_turnover"], 4),
            "weight_mean": round(st.mean(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_min": round(min(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_max": round(max(row["weight"] for row in overlay["weight_records"]), 4),
        }
        if phase in r112.PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec

    names = ("B_industry_relative_strength_h20_sl20", "overlay")
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
    return {
        "target": target,
        "long": long,
        "recent": recent,
        "annual_turnover_mean": annual_turnover_mean,
        "weight": {
            "mean": weight_mean,
            "min": weight_min,
            "max": weight_max,
        },
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
    }


def main() -> int:
    r114 = _load_r114()
    r112 = r114._load_r112()
    membership = r112._historical_membership()
    service = r112._service()

    results = {}
    for target in TARGETS:
        results[target] = _evaluate_target(r114, r112, membership, service, target)
        overlay = results[target]["long"]["overlay"]
        base = results[target]["long"]["B_industry_relative_strength_h20_sl20"]
        print(
            f"target={target:.3%}: annual={overlay['annual_mean']:.2%} "
            f"sharpe={overlay['sharpe_mean']:.3f} "
            f"mdd_mean={overlay['mdd_mean']:.2%} "
            f"mdd_worst={overlay['mdd_worst']:.2%} "
            f"turnover={results[target]['annual_turnover_mean']:.2f}",
            flush=True,
        )

    r114_result = json.loads(R114_JSON.read_text(encoding="utf-8"))
    r114_long = r114_result["long"]["B_vol_target_20pct"]

    passes = {}
    for target in TARGETS:
        overlay = results[target]["long"]["overlay"]
        base = results[target]["long"]["B_industry_relative_strength_h20_sl20"]
        recent_overlay = results[target]["recent"]["overlay"]
        e1 = bool(
            overlay["annual_mean"] >= 0.15
            and overlay["sharpe_mean"] >= 0.90
            and overlay["mdd_mean"] >= -0.225
            and overlay["mdd_worst"] >= -0.30
        )
        e2 = overlay["positive_phases"] == 10
        e3 = bool(
            recent_overlay["total_return_mean"] > 0
            and recent_overlay["positive_phases"] >= 4
        )
        e4 = bool(
            overlay["sharpe_mean"] >= base["sharpe_mean"] + 0.15
            and overlay["annual_mean"] >= base["annual_mean"] * 0.60
            and overlay["mdd_mean"] >= base["mdd_mean"] + 0.15
        )
        e5 = bool(0.30 <= results[target]["weight"]["mean"] <= 1.00)
        e6 = results[target]["annual_turnover_mean"] <= 1.5
        e7 = bool(
            overlay["mdd_worst"] >= r114_long["mdd_worst"] + 0.05
            and overlay["sharpe_mean"] >= r114_long["sharpe_mean"] - 0.03
        )
        gates = {
            "E1": e1,
            "E2": e2,
            "E3": e3,
            "E4": e4,
            "E5": e5,
            "E6": e6,
            "E7": e7,
        }
        gates["all_pass"] = all(gates.values())
        passes[target] = gates

    d1 = all(len(results[target]["phase_records"]) == 10 and len(results[target]["recent_records"]) == 5 for target in TARGETS)
    passing_targets = [target for target in TARGETS if passes[target]["all_pass"]]
    adjacent_pairs = [
        (0.125, 0.15),
        (0.15, 0.175),
    ]
    p1 = any(
        all(target in passing_targets for target in pair)
        for pair in adjacent_pairs
    ) and 0.15 in passing_targets
    p2 = all(
        results[target]["long"]["overlay"]["annual_mean"] >= 0.12
        and results[target]["long"]["overlay"]["sharpe_mean"] >= 0.80
        and results[target]["long"]["overlay"]["mdd_mean"] >= -0.25
        for target in TARGETS
    )
    p3 = all(results[target]["annual_turnover_mean"] <= 1.5 for target in TARGETS)
    decision = {
        "D1": d1,
        "P1": p1,
        "P2": p2,
        "P3": p3,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R116 volatility target sensitivity around 15%",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "targets": TARGETS,
        "results": results,
        "passes": passes,
        "passing_targets": passing_targets,
        "decision": decision,
        "final_recommendation": (
            "15% volatility target is neighborhood-robust"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "This is a sensitivity check around R115, not an independent confirmation.",
            "The industry sleeve still uses current industry snapshots backfilled over history.",
            "No production approval is implied by this evidence.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("passes", passes, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())