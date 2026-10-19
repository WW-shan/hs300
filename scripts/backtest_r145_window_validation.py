#!/usr/bin/env python3
"""R145: independent validation of the 40-day risk-parity window."""
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
OUT = EVIDENCE / "r145_window_validation.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R144_SCRIPT = ROOT / "scripts" / "backtest_r144_portfolio_sensitivity.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
R128_CACHE = DATA / "research" / "r128_cache"
R132_CACHE = DATA / "research" / "r132_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
BOUNDS = (0.2, 0.8)
OOS_START = date(2020, 12, 31)
OOS_END = date(2021, 6, 1)
RECENT_START = date(2025, 10, 14)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r144 = _load(R144_SCRIPT, "r144")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15

    def build(window: int, phase: date, a: pl.DataFrame, b: pl.DataFrame) -> pl.DataFrame:
        risk = r144._combine_window(a, b, window, BOUNDS)
        return r114._vol_target(risk)["curve"]

    results = {}
    for window in (40, 60):
        # 15-phase rolling distribution
        records = []
        for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
            key = phase.isoformat()
            a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
            b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
            cand = build(window, phase, a, b)
            records.extend(r127._window_stats(cand["value"].to_list()))

        # OOS segment
        a_oos = pl.read_parquet(R128_CACHE / f"{A_SLEEVE}_{OOS_START.isoformat()}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        b_oos = pl.read_parquet(R132_CACHE / f"{B_SLEEVE}_{OOS_START.isoformat()}_{r112.END.isoformat()}_pit.parquet").with_columns(pl.col("date").cast(pl.Date))
        cand_oos = build(window, OOS_START, a_oos, b_oos)
        oos_slice = cand_oos.filter((pl.col("date") >= OOS_START) & (pl.col("date") <= OOS_END)).sort("date")
        oos_ret = float(oos_slice["value"][-1] / oos_slice["value"][0] - 1.0)

        # recent year
        a_r = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{RECENT_START.isoformat()}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        b_r = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{RECENT_START.isoformat()}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        cand_r = build(window, RECENT_START, a_r, b_r)
        recent_stats = r112._stats(cand_r)

        results[f"w{window}"] = {
            "rolling": r127._summarize(records),
            "oos_return": round(oos_ret, 6),
            "recent": recent_stats,
        }
        print(f"w{window}: {json.dumps(results[f'w{window}'], ensure_ascii=False)}", flush=True)

    w40, w60 = results["w40"], results["w60"]
    d1 = all(v["rolling"]["windows"] >= 300 for v in results.values())
    g1 = w40["oos_return"] >= w60["oos_return"]
    g2 = w40["recent"]["total_return"] >= w60["recent"]["total_return"]
    g3 = w40["rolling"]["positive_rate"] >= w60["rolling"]["positive_rate"] + 0.02
    g4 = w40["rolling"]["return_worst"] >= w60["rolling"]["return_worst"]
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R145 independent validation of the 40-day risk-parity window",
        "results": results,
        "decision": decision,
        "caveats": ["The OOS segment is short and gate-closed throughout."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
