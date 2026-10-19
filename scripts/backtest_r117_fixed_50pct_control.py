#!/usr/bin/env python3
"""R117: fixed 50% exposure control for the historical B sleeve."""
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
R115_JSON = EVIDENCE / "r115_b_vol_target_15.json"
OUT = EVIDENCE / "r117_fixed_50pct_control.json"
FIXED_WEIGHT = 0.50
ROUND_TRIP_COST = 0.0052


def _load_r112():
    spec = importlib.util.spec_from_file_location("r112", R112_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixed_weight(b: pl.DataFrame):
    dates = b["date"].to_list()
    values = b["value"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]

    portfolio = 1.0
    sleeve = 1.0
    target = FIXED_WEIGHT
    rows = [{"date": dates[0], "value": portfolio}]
    total_turnover = 0.0

    for i in range(1, len(dates)):
        day = dates[i]
        ret = returns[i - 1]
        sleeve *= 1.0 + ret
        portfolio *= 1.0 + target * ret

        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total_value = target * sleeve + (1.0 - target)
            actual_weight = target * sleeve / total_value if total_value else target
            turnover = abs(target - actual_weight)
            total_turnover += turnover
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve = 1.0

        rows.append({"date": day, "value": portfolio})

    curve = pl.DataFrame(rows)
    annual_turnover = total_turnover * 252.0 / max(len(dates) - 1, 1)
    return {"curve": curve, "annual_turnover": annual_turnover, "total_turnover": total_turnover}


def main() -> int:
    r112 = _load_r112()
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
        fixed = _fixed_weight(b)
        rec = {
            "phase": key,
            "B_industry_relative_strength_h20_sl20": r112._stats(b),
            "B_fixed_50pct": r112._stats(fixed["curve"]),
            "annual_turnover": round(fixed["annual_turnover"], 4),
        }
        if phase in r112.PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec
        print(
            f"[{key}] B={rec['B_industry_relative_strength_h20_sl20']['total_return']:+.3f} "
            f"fixed50={rec['B_fixed_50pct']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = (
        "B_industry_relative_strength_h20_sl20",
        "B_fixed_50pct",
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

    r115 = json.loads(R115_JSON.read_text(encoding="utf-8"))
    r115_long = r115["long"]["B_vol_target_15pct"]
    fixed = long["B_fixed_50pct"]

    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    e1 = bool(
        fixed["annual_mean"] >= 0.15
        and fixed["sharpe_mean"] >= 0.90
        and fixed["mdd_mean"] >= -0.225
        and fixed["mdd_worst"] >= -0.30
    )
    e2 = fixed["positive_phases"] == 10
    e3 = bool(
        recent["B_fixed_50pct"]["total_return_mean"] > 0
        and recent["B_fixed_50pct"]["positive_phases"] >= 4
    )
    e4 = bool(
        abs(fixed["sharpe_mean"] - r115_long["sharpe_mean"]) <= 0.03
        and abs(fixed["mdd_mean"] - r115_long["mdd_mean"]) <= 0.02
    )
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R117 fixed 50% exposure control for the historical B sleeve",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeve": r112.SLEEVES["B_industry_relative_strength_h20_sl20"],
        "rule": {
            "fixed_weight": FIXED_WEIGHT,
            "rebalance": "monthly",
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "r115_comparison": {
            "vol_target_15": {
                "sharpe": r115_long["sharpe_mean"],
                "annual": r115_long["annual_mean"],
                "mdd_mean": r115_long["mdd_mean"],
                "mdd_worst": r115_long["mdd_worst"],
            },
            "delta": {
                "sharpe": round(fixed["sharpe_mean"] - r115_long["sharpe_mean"], 4),
                "annual": round(fixed["annual_mean"] - r115_long["annual_mean"], 4),
                "mdd_mean": round(fixed["mdd_mean"] - r115_long["mdd_mean"], 4),
                "mdd_worst": round(fixed["mdd_worst"] - r115_long["mdd_worst"], 4),
            },
        },
        "decision": decision,
        "final_recommendation": (
            "fixed 50% exposure can replace the dynamic target"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "This is a control experiment, not a new deployment candidate.",
            "The industry sleeve still uses current industry snapshots backfilled over history.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long fixed", long["B_fixed_50pct"], flush=True)
    print("recent fixed", recent["B_fixed_50pct"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())