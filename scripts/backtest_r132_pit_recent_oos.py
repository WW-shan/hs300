#!/usr/bin/env python3
"""R132: PIT-industry honest candidate, recent year + out-of-sample segment.

Recent year reuses R119/R125 caches.  The out-of-sample segment needs a PIT
B-sleeve run from 2020-12-31, so this script temporarily swaps the ext_data
industry fields (same mechanism as R119) and restores them afterwards.
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
OUT = EVIDENCE / "r132_pit_recent_oos.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R119_SCRIPT = ROOT / "scripts" / "backtest_r119_pit_industry_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
R128_CACHE = DATA / "research" / "r128_cache"
R132_CACHE = DATA / "research" / "r132_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
OOS_START = date(2020, 12, 31)
OOS_END = date(2021, 6, 1)
END = date(2026, 10, 16)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _slice(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def _ret(curve: pl.DataFrame) -> float:
    v = curve["value"].to_list()
    return v[-1] / v[0] - 1.0


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r119 = _load(R119_SCRIPT, "r119")
    r114.TARGET_VOL = 0.15

    # ---- Part 1: recent year (all caches) ----
    key = "2025-10-14"
    a_recent = pl.read_parquet(
        R125_CACHE / f"{A_SLEEVE}_{key}_{END.isoformat()}_3x.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    b_recent = pl.read_parquet(
        R119_CACHE / f"{B_SLEEVE}_{key}_{END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_recent, _ = r112._combine(a_recent, b_recent, "risk")
    cand_recent = r114._vol_target(risk_recent)["curve"]
    recent = {
        "window": [key, str(END)],
        "A_macro_gate_quality": r112._stats(a_recent),
        "B_pit_industry": r112._stats(b_recent),
        "dual_risk_parity": r112._stats(risk_recent),
        "candidate_pit_dual_vol15": r112._stats(cand_recent),
    }
    print("recent:", json.dumps(recent, ensure_ascii=False), flush=True)

    # ---- Part 2: out-of-sample segment with PIT industry ----
    R132_CACHE.mkdir(parents=True, exist_ok=True)
    b_oos_path = R132_CACHE / f"{B_SLEEVE}_{OOS_START.isoformat()}_{END.isoformat()}_pit.parquet"
    if b_oos_path.exists():
        b_full = pl.read_parquet(b_oos_path).with_columns(pl.col("date").cast(pl.Date))
    else:
        swap_info = r119._rebuild_industry_factors()
        print("swap:", json.dumps(swap_info, ensure_ascii=False), flush=True)
        try:
            membership = r112._historical_membership()
            service = r112._service()
            r112.CACHE = R132_CACHE
            b_full = r112._run_sleeve(
                service, membership, strategy_id=B_SLEEVE, start=OOS_START, end=END
            )
            b_full.write_parquet(b_oos_path)
        finally:
            r119._restore_ext()
            print("ext_data restored", flush=True)

    a_full = pl.read_parquet(
        R128_CACHE / f"{A_SLEEVE}_{OOS_START.isoformat()}_{END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_full, _ = r112._combine(a_full, b_full, "risk")
    cand_full = r114._vol_target(risk_full)["curve"]

    a_oos = _slice(a_full, OOS_START, OOS_END)
    b_oos = _slice(b_full, OOS_START, OOS_END)
    risk_oos = _slice(risk_full, OOS_START, OOS_END)
    cand_oos = _slice(cand_full, OOS_START, OOS_END)
    oos = {
        "window": [str(OOS_START), str(OOS_END)],
        "A_macro_gate_quality": {"return": round(_ret(a_oos), 6), "days": a_oos.height},
        "B_pit_industry": {"return": round(_ret(b_oos), 6), "days": b_oos.height},
        "dual_risk_parity": {"return": round(_ret(risk_oos), 6), "days": risk_oos.height},
        "candidate_pit_dual_vol15": r112._stats(cand_oos),
        "candidate_pit_full_period": r112._stats(cand_full),
    }
    print("oos:", json.dumps(oos, ensure_ascii=False), flush=True)

    d1 = recent["candidate_pit_dual_vol15"]["days"] > 200 and oos["candidate_pit_dual_vol15"]["days"] > 80
    g1 = recent["candidate_pit_dual_vol15"]["total_return"] > 0
    g2 = recent["candidate_pit_dual_vol15"]["mdd"] >= -0.15
    g3 = oos["candidate_pit_dual_vol15"]["total_return"] > -0.05
    g4 = oos["candidate_pit_dual_vol15"]["mdd"] >= -0.15
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R132 PIT-industry honest candidate, recent year + out-of-sample",
        "recent": recent,
        "out_of_sample": oos,
        "decision": decision,
        "caveats": [
            "The OOS segment is short and the macro gate is closed throughout.",
            "PIT industry coverage is 99.72%.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
