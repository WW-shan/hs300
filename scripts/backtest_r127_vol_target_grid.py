#!/usr/bin/env python3
"""R127: parameter sensitivity grid for the dual-sleeve + vol-target candidate.

Grid: target vol {12.5, 15, 17.5, 20}% x risk-parity weight bounds
{[0.2,0.8], [0.3,0.7], [0.4,0.6]}.  Controls: fixed 50/50 + 15%,
A-only + 15%, B-only + 15%.

All inputs are the R125 (fixed A) and R112 caches; no strategy parameters
are changed.
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
OUT = EVIDENCE / "r127_vol_target_grid.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
WINDOW = 252
ROUND_TRIP_COST = 0.0052
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
TARGETS = [0.125, 0.15, 0.175, 0.20]
BOUNDS = [(0.2, 0.8), (0.3, 0.7), (0.4, 0.6)]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _combine_bounds(a: pl.DataFrame, b: pl.DataFrame, mode: str, bounds: tuple[float, float]):
    """Copy of r112._combine with parameterized risk-parity weight bounds."""
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

    lo, hi = bounds
    targets = []
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if mode == "fixed" or i < 60:
                weight = 0.5
            else:
                window_a = [0.0, *returns_a][i - 60 : i]
                window_b = [0.0, *returns_b][i - 60 : i]
                vol_a = max(st.pstdev(window_a), 1e-6)
                vol_b = max(st.pstdev(window_b), 1e-6)
                weight = (1.0 / vol_a) / (1.0 / vol_a + 1.0 / vol_b)
                weight = min(hi, max(lo, weight))
            targets.append((day, weight))
            prev_month = month
    target_map = {day: weight for day, weight in targets}

    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    target = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    for i in range(1, len(dates)):
        day = dates[i]
        ret_a = returns_a[i - 1]
        ret_b = returns_b[i - 1]
        sleeve_a *= 1.0 + ret_a
        sleeve_b *= 1.0 + ret_b
        portfolio *= 1.0 + target * ret_a + (1.0 - target) * ret_b
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve_a + (1.0 - target) * sleeve_b
            actual_a = target * sleeve_a / total if total else target
            actual_b = 1.0 - actual_a
            next_target = target_map[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * (abs(next_target - actual_a) + abs((1.0 - next_target) - actual_b))
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            target = next_target
        rows.append({"date": day, "value": portfolio})
    return pl.DataFrame(rows)


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

    curves = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R112_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        curves[key] = (a, b)

    def evaluate(label: str, builder) -> dict:
        records = []
        for key, (a, b) in curves.items():
            curve = builder(a, b)
            records.extend(_window_stats(curve["value"].to_list()))
        summary = _summarize(records)
        print(f"{label}: {json.dumps(summary, ensure_ascii=False)}", flush=True)
        return summary

    grid = {}
    for target in TARGETS:
        for bounds in BOUNDS:
            label = f"vol{target * 100:g}pct_bounds{int(bounds[0] * 100)}-{int(bounds[1] * 100)}"
            def builder(a, b, target=target, bounds=bounds):
                r114.TARGET_VOL = target
                combined = _combine_bounds(a, b, "risk", bounds)
                return r114._vol_target(combined)["curve"]
            grid[label] = evaluate(label, builder)

    controls = {}
    def fixed_builder(a, b):
        r114.TARGET_VOL = 0.15
        combined = _combine_bounds(a, b, "fixed", (0.2, 0.8))
        return r114._vol_target(combined)["curve"]
    controls["fixed50_50_vol15"] = evaluate("fixed50_50_vol15", fixed_builder)

    def a_only(a, b):
        r114.TARGET_VOL = 0.15
        return r114._vol_target(a)["curve"]
    controls["A_only_vol15"] = evaluate("A_only_vol15", a_only)

    def b_only(a, b):
        r114.TARGET_VOL = 0.15
        return r114._vol_target(b)["curve"]
    controls["B_only_vol15"] = evaluate("B_only_vol15", b_only)

    def _pass(entry, key, threshold):
        return entry.get(key, -9) >= threshold

    d1 = len(grid) == 12 and len(controls) == 3 and all(v["windows"] >= 300 for v in grid.values())
    g1 = sum(1 for v in grid.values() if v["positive_rate"] >= 0.85) >= 8
    g2 = sum(1 for v in grid.values() if v["return_worst"] >= -0.25) >= 8
    g3 = sum(1 for v in grid.values() if v["return_p05"] >= -0.15) >= 6
    rp15 = grid["vol15pct_bounds20-80"]
    g4 = rp15["positive_rate"] >= controls["fixed50_50_vol15"]["positive_rate"]
    g5 = rp15["positive_rate"] >= max(controls["A_only_vol15"]["positive_rate"], controls["B_only_vol15"]["positive_rate"])
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R127 volatility-target and risk-parity-bounds sensitivity grid",
        "universe": "R125 fixed A sleeve + R112 B sleeve caches, 3x cost",
        "grid": {"targets": TARGETS, "bounds": BOUNDS},
        "results": grid,
        "controls": controls,
        "decision": decision,
        "caveats": [
            "Overlapping windows; descriptive only.",
            "Grid is exploratory; no parameter is promoted by it.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
