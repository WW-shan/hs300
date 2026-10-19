#!/usr/bin/env python3
"""R134: parameter grid for the PIT-industry honest candidate.

Same grid as R127 but with the R119 PIT B sleeve instead of the current
snapshot sleeve.
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
OUT = EVIDENCE / "r134_pit_grid.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
WINDOW = 252
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


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")

    curves = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        curves[key] = (a, b)

    def evaluate(label, builder):
        records = []
        for key, (a, b) in curves.items():
            curve = builder(a, b)
            records.extend(r127._window_stats(curve["value"].to_list()))
        summary = r127._summarize(records)
        print(f"{label}: {json.dumps(summary, ensure_ascii=False)}", flush=True)
        return summary

    grid = {}
    for target in TARGETS:
        for bounds in BOUNDS:
            label = f"vol{target * 100:g}pct_bounds{int(bounds[0] * 100)}-{int(bounds[1] * 100)}"
            def builder(a, b, target=target, bounds=bounds):
                r114.TARGET_VOL = target
                combined = r127._combine_bounds(a, b, "risk", bounds)
                return r114._vol_target(combined)["curve"]
            grid[label] = evaluate(label, builder)

    controls = {}
    def fixed_builder(a, b):
        r114.TARGET_VOL = 0.15
        combined = r127._combine_bounds(a, b, "fixed", (0.2, 0.8))
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

    base = grid["vol15pct_bounds20-80"]
    d1 = len(grid) == 12 and len(controls) == 3 and abs(base["positive_rate"] - 0.8604) <= 0.001
    g1 = sum(1 for v in grid.values() if v["positive_rate"] >= 0.83) >= 8
    g2 = all(v["return_worst"] >= -0.25 for v in grid.values())
    g3 = base["positive_rate"] >= max(controls["A_only_vol15"]["positive_rate"], controls["B_only_vol15"]["positive_rate"]) - 0.01
    g4 = base["positive_rate"] >= 0.80 and controls["fixed50_50_vol15"]["positive_rate"] >= 0.80
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R134 PIT honest candidate parameter grid",
        "universe": "R125 A cache + R119 PIT B cache, 3x cost",
        "grid": {"targets": TARGETS, "bounds": BOUNDS},
        "results": grid,
        "controls": controls,
        "decision": decision,
        "caveats": ["Overlapping windows; descriptive only.", "Grid is exploratory."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
