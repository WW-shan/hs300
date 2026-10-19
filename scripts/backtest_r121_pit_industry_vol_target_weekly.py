#!/usr/bin/env python3
"""R121: weekly rebalancing for the 15% volatility target under PIT industry.

Reuses the R119 PIT-industry B-sleeve caches.  The only change versus R119 is
that the exposure target is recomputed on the first trading day of each week
instead of each month.
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
R119_JSON = EVIDENCE / "r119_pit_industry_vol_target.json"
OUT = EVIDENCE / "r121_pit_industry_vol_target_weekly.json"
R119_CACHE = DATA / "research" / "r119_cache"
TARGET_VOL = 0.15
MIN_WEIGHT = 0.20
MAX_WEIGHT = 1.00
ROUND_TRIP_COST = 0.0052


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _weekly_vol_target(b: pl.DataFrame):
    dates = b["date"].to_list()
    values = b["value"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]

    portfolio = 1.0
    sleeve = 1.0
    target = 0.5
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight": target}]
    total_turnover = 0.0

    for i in range(1, len(dates)):
        day = dates[i]
        ret = returns[i - 1]
        sleeve *= 1.0 + ret
        portfolio *= 1.0 + target * ret

        # Week boundary: ISO year/week changes at the next trading day.
        next_week = i + 1 == len(dates) or dates[i + 1].isocalendar()[:2] != day.isocalendar()[:2]
        if next_week:
            total_value = target * sleeve + (1.0 - target)
            actual_weight = target * sleeve / total_value if total_value else target

            if i + 1 < len(dates):
                if i < 60:
                    next_target = 0.5
                else:
                    window = returns[i - 60:i]
                    realized_vol = st.pstdev(window) * math.sqrt(252.0)
                    if realized_vol <= TARGET_VOL:
                        next_target = MAX_WEIGHT
                    else:
                        next_target = min(
                            MAX_WEIGHT,
                            max(MIN_WEIGHT, TARGET_VOL / realized_vol),
                        )
            else:
                next_target = target

            turnover = abs(next_target - actual_weight)
            total_turnover += turnover
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve = 1.0
            target = next_target

        rows.append({"date": day, "value": portfolio})
        if next_week and i + 1 < len(dates):
            weight_records.append({"date": dates[i + 1], "weight": target})

    curve = pl.DataFrame(rows)
    annual_turnover = total_turnover * 252.0 / max(len(dates) - 1, 1)
    return {
        "curve": curve,
        "weight_records": weight_records,
        "total_turnover": total_turnover,
        "annual_turnover": annual_turnover,
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]

    phase_outputs = {}
    recent_outputs = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R119_CACHE / f"{strategy_id}_{phase.isoformat()}_{r112.END.isoformat()}.parquet"
        if not path.exists():
            raise RuntimeError(f"missing R119 cache: {path}")
        b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        overlay = _weekly_vol_target(b)
        rec = {
            "phase": key,
            "B_pit_industry": r112._stats(b),
            "B_pit_industry_vol_target_15pct_weekly": r112._stats(overlay["curve"]),
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
            f"weekly={rec['B_pit_industry_vol_target_15pct_weekly']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = ("B_pit_industry", "B_pit_industry_vol_target_15pct_weekly")
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
    overlay = long["B_pit_industry_vol_target_15pct_weekly"]
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
        recent["B_pit_industry_vol_target_15pct_weekly"]["total_return_mean"] > 0
        and recent["B_pit_industry_vol_target_15pct_weekly"]["positive_phases"] >= 4
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
        "purpose": "R121 weekly rebalancing for the 15% volatility target under PIT industry",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeve": strategy_id,
        "industry": r119.get("industry"),
        "rule": {
            "lookback_days": 60,
            "target_annual_volatility": TARGET_VOL,
            "weight_bounds": [MIN_WEIGHT, MAX_WEIGHT],
            "rebalance": "weekly",
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "weight": {"mean": weight_mean, "min": weight_min, "max": weight_max},
        "r119_comparison": {
            "monthly_15": {
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
            "weekly rebalancing solves the worst-phase drawdown"
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
    print("long overlay", long["B_pit_industry_vol_target_15pct_weekly"], flush=True)
    print("recent overlay", recent["B_pit_industry_vol_target_15pct_weekly"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())