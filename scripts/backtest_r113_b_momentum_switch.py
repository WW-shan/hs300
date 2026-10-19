#!/usr/bin/env python3
"""R113: switch between the historical B sleeve and the A sleeve.

The rule is deliberately minimal:

- B trailing 60-day return >= 0 -> 80% B / 20% A
- B trailing 60-day return < 0  -> 20% B / 80% A
- Before 60 days of history      -> 50% B / 50% A

The switch is evaluated monthly and pays 0.52% on one-way turnover.  All sleeve
curves, costs, execution assumptions and historical membership handling come
from R112.
"""
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
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
OUT = EVIDENCE / "r113_b_momentum_switch.json"
ROUND_TRIP_COST = 0.0052


def _load_r112():
    spec = importlib.util.spec_from_file_location("r112", R112_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _combine_switch(a: pl.DataFrame, b: pl.DataFrame):
    joined = (
        a.rename({"value": "a"})
        .join(b.rename({"value": "b"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values_a = joined["a"].to_list()
    values_b = joined["b"].to_list()
    returns_a = [values_a[i] / values_a[i - 1] - 1.0 for i in range(1, len(values_a))]
    returns_b = [values_b[i] / values_b[i - 1] - 1.0 for i in range(1, len(values_b))]

    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    target_a = 0.5
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight_a": target_a}]
    total_turnover = 0.0
    high_b_months = 0
    low_b_months = 0

    for i in range(1, len(dates)):
        day = dates[i]
        ret_a = returns_a[i - 1]
        ret_b = returns_b[i - 1]
        sleeve_a *= 1.0 + ret_a
        sleeve_b *= 1.0 + ret_b
        portfolio *= 1.0 + target_a * ret_a + (1.0 - target_a) * ret_b

        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target_a * sleeve_a + (1.0 - target_a) * sleeve_b
            actual_a = target_a * sleeve_a / total if total else target_a
            actual_b = 1.0 - actual_a

            if i + 1 < len(dates):
                if i < 60:
                    next_target_a = 0.5
                else:
                    b_momentum = values_b[i] / values_b[i - 60] - 1.0
                    next_target_a = 0.2 if b_momentum >= 0.0 else 0.8
                    if b_momentum >= 0.0:
                        high_b_months += 1
                    else:
                        low_b_months += 1
            else:
                next_target_a = target_a

            turnover = 0.5 * (
                abs(next_target_a - actual_a) + abs((1.0 - next_target_a) - actual_b)
            )
            total_turnover += turnover
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            target_a = next_target_a

        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weight_records.append({"date": dates[i + 1], "weight_a": target_a})

    curve = pl.DataFrame(rows)
    annual_turnover = total_turnover * 252.0 / max(len(dates) - 1, 1)
    stats = {
        "curve": curve,
        "weight_records": weight_records,
        "total_turnover": total_turnover,
        "annual_turnover": annual_turnover,
        "high_b_months": high_b_months,
        "low_b_months": low_b_months,
    }
    return stats


def main() -> int:
    r112 = _load_r112()
    membership = r112._historical_membership()
    service = r112._service()

    phase_outputs = {}
    recent_outputs = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = r112._run_sleeve(
            service,
            membership,
            strategy_id=r112.SLEEVES["A_macro_gate_quality"],
            start=phase,
            end=r112.END,
        )
        b = r112._run_sleeve(
            service,
            membership,
            strategy_id=r112.SLEEVES["B_industry_relative_strength_h20_sl20"],
            start=phase,
            end=r112.END,
        )
        switched = _combine_switch(a, b)
        rec = {
            "phase": key,
            "A_macro_gate_quality": r112._stats(a),
            "B_industry_relative_strength_h20_sl20": r112._stats(b),
            "b_momentum_switch": r112._stats(switched["curve"]),
            "annual_turnover": round(switched["annual_turnover"], 4),
            "weight_a_mean": round(st.mean(row["weight_a"] for row in switched["weight_records"]), 4),
            "weight_a_min": round(min(row["weight_a"] for row in switched["weight_records"]), 4),
            "weight_a_max": round(max(row["weight_a"] for row in switched["weight_records"]), 4),
            "high_b_months": switched["high_b_months"],
            "low_b_months": switched["low_b_months"],
            "correlation": round(r112._corr(a, b), 4),
        }
        if phase in r112.PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec
        print(
            f"[{key}] B={rec['B_industry_relative_strength_h20_sl20']['total_return']:+.3f} "
            f"switch={rec['b_momentum_switch']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = (
        "A_macro_gate_quality",
        "B_industry_relative_strength_h20_sl20",
        "b_momentum_switch",
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
    weight_a_mean = round(
        st.mean(phase_outputs[key]["weight_a_mean"] for key in phase_outputs), 4
    )
    weight_a_min = round(
        min(phase_outputs[key]["weight_a_min"] for key in phase_outputs), 4
    )
    weight_a_max = round(
        max(phase_outputs[key]["weight_a_max"] for key in phase_outputs), 4
    )

    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    switch = long["b_momentum_switch"]
    base = long["B_industry_relative_strength_h20_sl20"]
    e1 = bool(
        switch["annual_mean"] >= 0.15
        and switch["sharpe_mean"] >= 0.83
        and switch["mdd_mean"] >= -0.275
        and switch["mdd_worst"] >= -0.32
    )
    e2 = switch["positive_phases"] == 10
    e3 = bool(
        recent["b_momentum_switch"]["total_return_mean"] > 0
        and recent["b_momentum_switch"]["positive_phases"] >= 4
    )
    e4 = bool(
        switch["sharpe_mean"] >= base["sharpe_mean"] + 0.05
        and switch["annual_mean"] >= base["annual_mean"] * 0.60
        and switch["mdd_mean"] >= base["mdd_mean"] + 0.10
    )
    e5 = bool(0.20 <= 1.0 - weight_a_mean <= 0.80)
    e6 = annual_turnover_mean <= 1.5
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
        "E5": e5,
        "E6": e6,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R113 B-sleeve momentum switch to A under historical HS300 membership",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeves": r112.SLEEVES,
        "rule": {
            "lookback_days": 60,
            "high_b_weights": {"A": 0.2, "B": 0.8},
            "low_b_weights": {"A": 0.8, "B": 0.2},
            "initial_weights": {"A": 0.5, "B": 0.5},
            "rebalance": "monthly",
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "weight_a": {
            "mean": weight_a_mean,
            "min": weight_a_min,
            "max": weight_a_max,
        },
        "decision": decision,
        "final_recommendation": (
            "R113 B-momentum switch candidate for forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "The industry sleeve still uses current industry snapshots backfilled over history.",
            "Recent fresh-start paths may share the same macro-gate exposure period.",
            "The 3x cost convention is a stress test, not an execution-cost estimate.",
            "No production approval is implied by this evidence.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long switch", long["b_momentum_switch"], flush=True)
    print("recent switch", recent["b_momentum_switch"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())