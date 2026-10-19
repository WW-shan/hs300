#!/usr/bin/env python3
"""R165: tail-aware activity budget when the A sleeve is flat/cash."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r165_tail_aware_activity_budget.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R164_SCRIPT = ROOT / "scripts" / "backtest_r164_activity_aware_risk_parity.py"
CACHE = DATA / "research" / "r163_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2018, 1, 2)
END = date(2026, 10, 16)
FLAT_VOL = 0.02
FLAT_A = 0.20
FLAT_B = 0.40
FLAT_CASH = 0.40
ROUND_TRIP_COST = 0.0052


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _monthly_targets(dates: list[date], returns_a: list[float], returns_b: list[float]):
    targets = []
    flat_targets = []
    weight_a = 0.5
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            vol_a_20 = 0.0
            if i >= 20:
                vol_a_20 = st.pstdev(returns_a[i - 20 : i]) * math.sqrt(252.0)
            if i >= 60 and vol_a_20 < FLAT_VOL:
                weight_a = FLAT_A
                weight_b = FLAT_B
                weight_cash = FLAT_CASH
                flat_targets.append({
                    "date": day,
                    "vol_a_20": round(vol_a_20, 6),
                    "weight_a": weight_a,
                    "weight_b": weight_b,
                    "weight_cash": weight_cash,
                })
            elif i < 60:
                weight_a = 0.5
                weight_b = 0.5
                weight_cash = 0.0
            else:
                window_a = returns_a[i - 60 : i]
                window_b = returns_b[i - 60 : i]
                vol_a = max(st.pstdev(window_a), 1e-6)
                vol_b = max(st.pstdev(window_b), 1e-6)
                weight_a = (1.0 / vol_a) / (1.0 / vol_a + 1.0 / vol_b)
                weight_a = min(0.8, max(0.2, weight_a))
                weight_b = 1.0 - weight_a
                weight_cash = 0.0
            targets.append((day, weight_a, weight_b, weight_cash))
            prev_month = month
    return targets, flat_targets


def _combine_tail_budget(a: pl.DataFrame, b: pl.DataFrame):
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
    targets, flat_targets = _monthly_targets(dates, [0.0, *returns_a], [0.0, *returns_b])
    target_map = {day: (wa, wb, wc) for day, wa, wb, wc in targets}

    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    cash = 1.0
    target_a, target_b, target_cash = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{
        "date": dates[0], "weight_a": target_a, "weight_b": target_b, "weight_cash": target_cash,
    }]
    total_turnover = 0.0
    for i in range(1, len(dates)):
        day = dates[i]
        ret_a = returns_a[i - 1]
        ret_b = returns_b[i - 1]
        sleeve_a *= 1.0 + ret_a
        sleeve_b *= 1.0 + ret_b
        portfolio *= 1.0 + target_a * ret_a + target_b * ret_b

        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target_a * sleeve_a + target_b * sleeve_b + target_cash * cash
            actual_a = target_a * sleeve_a / total if total else target_a
            actual_b = target_b * sleeve_b / total if total else target_b
            actual_cash = target_cash * cash / total if total else target_cash
            next_target = target_map[dates[i + 1]] if i + 1 < len(dates) else (target_a, target_b, target_cash)
            turnover = 0.5 * (
                abs(next_target[0] - actual_a)
                + abs(next_target[1] - actual_b)
                + abs(next_target[2] - actual_cash)
            )
            total_turnover += turnover
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            cash = 1.0
            target_a, target_b, target_cash = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weight_records.append({
                "date": dates[i + 1],
                "weight_a": target_a,
                "weight_b": target_b,
                "weight_cash": target_cash,
            })
    return pl.DataFrame(rows), weight_records, flat_targets, total_turnover


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r164 = _load(R164_SCRIPT, "r164")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(CACHE / f"{A_SLEEVE}_{START.isoformat()}_{END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    b = pl.read_parquet(CACHE / f"{B_SLEEVE}_{START.isoformat()}_{END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")

    baseline_risk, _ = r112._combine(a, b, "risk")
    baseline = r114._vol_target(baseline_risk)["curve"].sort("date")
    candidate_risk, weights, flat_targets, total_turnover = _combine_tail_budget(a, b)
    candidate = r114._vol_target(candidate_risk)["curve"].sort("date")

    base_roll = r127._summarize(r127._window_stats(baseline["value"].to_list()))
    base_boot = r164._bootstrap(baseline["value"].to_list())
    cand_roll = r127._summarize(r127._window_stats(candidate["value"].to_list()))
    cand_boot = r164._bootstrap(candidate["value"].to_list())

    base_2018_2020 = r112._stats(r164._segment(baseline, date(2018, 1, 1), date(2020, 12, 31)))
    base_2024_2026 = r112._stats(r164._segment(baseline, date(2024, 1, 1), date(2026, 12, 31)))
    cand_2018_2020 = r112._stats(r164._segment(candidate, date(2018, 1, 1), date(2020, 12, 31)))
    cand_2024_2026 = r112._stats(r164._segment(candidate, date(2024, 1, 1), date(2026, 12, 31)))

    flat_weight_mean = st.mean(row["weight_a"] for row in flat_targets) if flat_targets else None
    flat_risk_mean = st.mean(row["weight_a"] + row["weight_b"] for row in flat_targets) if flat_targets else None
    d1 = candidate.height > 2000 and flat_weight_mean is not None
    d2 = (
        flat_weight_mean is not None
        and flat_risk_mean is not None
        and flat_weight_mean <= 0.25
        and flat_risk_mean <= 0.65
    )
    g1 = cand_roll["positive_rate"] >= 0.70
    g2 = cand_boot["positive_rate"] >= base_boot["positive_rate"]
    g3 = r112._stats(candidate)["mdd"] >= -0.18
    g4 = cand_2018_2020["total_return"] >= 0.155
    g5 = cand_2024_2026["total_return"] >= base_2024_2026["total_return"] - 0.10
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R165 tail-aware activity budget: 20% A + 40% B + 40% cash when A is flat",
        "window": [str(START), str(END)],
        "rule": {
            "flat_vol_threshold": FLAT_VOL,
            "flat_weights": {"a": FLAT_A, "b": FLAT_B, "cash": FLAT_CASH},
            "lookback_flat": 20,
            "lookback_risk_parity": 60,
            "weight_bounds": [0.2, 0.8],
            "rebalance": "monthly",
            "round_trip_cost": ROUND_TRIP_COST,
            "vol_target": 0.15,
        },
        "baseline": {
            "full": r112._stats(baseline),
            "2018_2020": base_2018_2020,
            "2024_2026": base_2024_2026,
            "rolling_1y": base_roll,
            "bootstrap": base_boot,
        },
        "candidate": {
            "full": r112._stats(candidate),
            "2018_2020": cand_2018_2020,
            "2024_2026": cand_2024_2026,
            "rolling_1y": cand_roll,
            "bootstrap": cand_boot,
            "flat_months": len(flat_targets),
            "flat_weight_a_mean": round(flat_weight_mean, 4) if flat_weight_mean is not None else None,
            "flat_risk_budget_mean": round(flat_risk_mean, 4) if flat_risk_mean is not None else None,
            "annual_turnover": round(total_turnover * 252.0 / max(candidate.height - 1, 1), 4),
        },
        "decision": decision,
        "flat_targets_sample": [
            {**row, "date": row["date"].isoformat()} for row in flat_targets[:20]
        ],
        "weight_records": [
            {**row, "date": row["date"].isoformat()} for row in weights
        ],
        "caveats": [
            "The flat-A detector uses only trailing 20-day sleeve returns.",
            "The cash leg earns zero return; this is a conservative research assumption.",
            "The result is a research candidate, not a production recommendation.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("baseline:", json.dumps(result["baseline"], ensure_ascii=False), flush=True)
    print("candidate:", json.dumps(result["candidate"], ensure_ascii=False), flush=True)
    print("flat months:", len(flat_targets), "weight A:", flat_weight_mean, "risk budget:", flat_risk_mean, flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
