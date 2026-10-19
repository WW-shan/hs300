#!/usr/bin/env python3
"""R128: recent-year actual window + true out-of-sample segment.

Part 1 reuses the R125 A cache and R112 B cache for the 2025-10-14 phase
(the window a user would actually have lived through).

Part 2 runs both sleeves from 2020-12-31 (the first day of the historical
membership snapshots) to 2026-10-16, which no earlier round used, and
evaluates 2020-12-31..2021-06-01.  The macro gate never opened in that
segment, so it doubles as a stress test of the candidate when the A sleeve
is flat.
"""
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
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r128_recent_and_oos.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
R128_CACHE = DATA / "research" / "r128_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
OOS_START = date(2020, 12, 31)
OOS_END = date(2021, 6, 1)
END = date(2026, 10, 16)
COST_3X = (0.0006, 0.0006, 0.001, 15.0)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _slice(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def _run_leg(service, membership, strategy_id: str, start: date, end: date) -> pl.DataFrame:
    R128_CACHE.mkdir(parents=True, exist_ok=True)
    path = R128_CACHE / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}.parquet"
    if path.exists():
        return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
    fees, comm, stamp, slip = COST_3X
    cfg = _load(R112_SCRIPT, "r112_cfg").StrategyBacktestConfig(
        strategy_id=strategy_id, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=fees, commission_pct=comm, stamp_tax_pct=stamp, slippage_bps=slip,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    r112 = _load(R112_SCRIPT, "r112_run")
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id} {start}: {result.error}")
    frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    print(f"{strategy_id} {start}: days={frame.height} ret={result.stats.get('total_return')}", flush=True)
    return frame


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    membership = r112._historical_membership()
    service = r112._service()

    # ---- Part 1: recent year ----
    key = "2025-10-14"
    a_recent = pl.read_parquet(
        R125_CACHE / f"{A_SLEEVE}_{key}_{END.isoformat()}_3x.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    b_recent = pl.read_parquet(
        R112_CACHE / f"{B_SLEEVE}_{key}_{END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_recent, _ = r112._combine(a_recent, b_recent, "risk")
    cand_recent = r114._vol_target(risk_recent)["curve"]
    recent = {
        "window": [key, str(END)],
        "A_macro_gate_quality": r112._stats(a_recent),
        "B_industry_relative_strength": r112._stats(b_recent),
        "dual_risk_parity": r112._stats(risk_recent),
        "candidate_dual_vol15": r112._stats(cand_recent),
    }
    print("recent:", json.dumps(recent, ensure_ascii=False), flush=True)

    # ---- Part 2: out-of-sample segment ----
    a_full = _run_leg(service, membership, A_SLEEVE, OOS_START, END)
    b_full = _run_leg(service, membership, B_SLEEVE, OOS_START, END)
    risk_full, _ = r112._combine(a_full, b_full, "risk")
    cand_full = r114._vol_target(risk_full)["curve"]

    a_oos = _slice(a_full, OOS_START, OOS_END)
    b_oos = _slice(b_full, OOS_START, OOS_END)
    risk_oos = _slice(risk_full, OOS_START, OOS_END)
    cand_oos = _slice(cand_full, OOS_START, OOS_END)

    def _ret(curve: pl.DataFrame) -> float:
        v = curve["value"].to_list()
        return v[-1] / v[0] - 1.0

    oos = {
        "window": [str(OOS_START), str(OOS_END)],
        "A_macro_gate_quality": {"return": round(_ret(a_oos), 6), "days": a_oos.height},
        "B_industry_relative_strength": {"return": round(_ret(b_oos), 6), "days": b_oos.height},
        "dual_risk_parity": {"return": round(_ret(risk_oos), 6), "days": risk_oos.height},
        "candidate_dual_vol15": r112._stats(cand_oos),
        "candidate_full_period": r112._stats(cand_full),
    }
    print("oos:", json.dumps(oos, ensure_ascii=False), flush=True)

    d1 = recent["candidate_dual_vol15"]["days"] > 200 and oos["candidate_dual_vol15"]["days"] > 80
    g1 = recent["candidate_dual_vol15"]["total_return"] > 0
    g2 = recent["candidate_dual_vol15"]["mdd"] >= -0.15
    g3 = oos["candidate_dual_vol15"]["total_return"] > -0.05
    g4 = oos["candidate_dual_vol15"]["mdd"] >= -0.15
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R128 recent-year actual window and true out-of-sample segment",
        "recent": recent,
        "out_of_sample": oos,
        "decision": decision,
        "caveats": [
            "The OOS segment is short (about 5 months) and the macro gate is closed throughout, so the A sleeve is flat; it tests the B-driven path only.",
            "The recent-year phase is part of the R126/R127 sample, not independent.",
            "No production approval is implied.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
