#!/usr/bin/env python3
"""R160: unify rolling 1-year probability conventions and run a block bootstrap."""
from __future__ import annotations

import importlib.util
import json
import random
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r160_window_conventions.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R159_CACHE = DATA / "research" / "r159_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252
BLOCK = 20
PATHS = 2000
SEED = 20261020


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _q(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))]


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(R159_CACHE / f"{A_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    b = pl.read_parquet(R159_CACHE / f"{B_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    risk, _ = r112._combine(a, b, "risk")
    cand = r114._vol_target(risk)["curve"].sort("date")
    values = cand["value"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]

    # A: multi-phase aggregate from R131 evidence
    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    a_rate = r131["candidate_pit_dual_vol15"]["positive_rate"]

    # B: single-phase overlapping
    b_windows = [values[i + WINDOW] / values[i] - 1.0 for i in range(len(values) - WINDOW)]
    b_rate = round(sum(v > 0 for v in b_windows) / len(b_windows), 4)

    # C: single-phase non-overlapping
    c_windows = []
    start = 0
    while start + WINDOW < len(values):
        c_windows.append(values[start + WINDOW] / values[start] - 1.0)
        start += WINDOW
    c_rate = round(sum(v > 0 for v in c_windows) / len(c_windows), 4) if c_windows else None

    # D: block bootstrap
    rng = random.Random(SEED)
    n_blocks = (WINDOW + BLOCK - 1) // BLOCK
    max_start = len(returns) - BLOCK
    paths = []
    for _ in range(PATHS):
        path = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start)
            path.extend(returns[s : s + BLOCK])
        path = path[:WINDOW]
        val = 1.0
        for r in path:
            val *= 1.0 + r
        paths.append(val - 1.0)
    d_rate = round(sum(v > 0 for v in paths) / len(paths), 4)
    d = {
        "positive_rate": d_rate,
        "p05": round(_q(paths, 0.05), 4),
        "median": round(st.median(paths), 4),
        "worst": round(min(paths), 4),
        "best": round(max(paths), 4),
    }

    d1 = all(x is not None for x in (a_rate, b_rate, c_rate, d_rate)) and len(paths) >= 2000
    g1 = c_rate >= 0.60
    g2 = d_rate >= 0.70
    g3 = d["p05"] >= -0.15
    g4 = max(b_rate, c_rate, d_rate) - min(b_rate, c_rate, d_rate) <= 0.15
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R160 unify rolling 1-year probability conventions",
        "convention_A_multiphase_overlapping": a_rate,
        "convention_B_singlephase_overlapping": b_rate,
        "convention_C_singlephase_nonoverlapping": c_rate,
        "convention_C_windows": len(c_windows),
        "convention_D_block_bootstrap": d,
        "bootstrap": {"block": BLOCK, "paths": PATHS, "seed": SEED},
        "decision": decision,
        "caveats": ["Bootstrap preserves 20-day autocorrelation only; not a full market-regime model."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("A multi-phase:", a_rate, flush=True)
    print("B single-phase overlapping:", b_rate, flush=True)
    print("C single-phase non-overlapping:", c_rate, "windows:", len(c_windows), flush=True)
    print("D bootstrap:", json.dumps(d, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
