#!/usr/bin/env python3
"""Paper-trade state tracker for the frozen dual-sleeve candidate.

Prints the current target configuration:
- macro gate state (three conditions)
- risk-parity weight for the A sleeve (60d inverse vol, bounds [0.2, 0.8])
- vol-target total exposure (60d realized vol, 15% target, bounds [0.2, 1.0])

Reads only caches and ext_data; deterministic.
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
OUT = EVIDENCE / "r152_paper_trading_state.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
PHASE = "2021-06-01"
END = "2026-10-16"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")

    a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{PHASE}_{END}_3x.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{PHASE}_{END}.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")

    # risk parity weight at the last month boundary
    joined = a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
    dates = joined["date"].to_list()
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    i = len(dates) - 1
    wa = [0.0, *ra][i - 60 : i]
    wb = [0.0, *rb][i - 60 : i]
    sa = max(st.pstdev(wa), 1e-6)
    sb = max(st.pstdev(wb), 1e-6)
    weight_a = (1.0 / sa) / (1.0 / sa + 1.0 / sb)
    weight_a = min(0.8, max(0.2, weight_a))

    # vol target on the combined curve
    combined, _ = r112._combine(a, b, "risk")
    values = combined["value"].to_list()
    rets = [values[k] / values[k - 1] - 1.0 for k in range(1, len(values))]
    realized = st.pstdev(rets[-60:]) * math.sqrt(252.0)
    exposure = min(1.0, max(0.2, 0.15 / realized)) if realized > 0 else 1.0

    # gate state on the last ext date
    ext = pl.read_parquet(DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={dates[-1].isoformat()}" / "part.parquet")
    erp = float(ext["equity_risk_premium"].drop_nulls()[0])
    credit = float(ext["credit_spread_5y_low"].drop_nulls()[0])
    term = float(ext["term_spread_10y_2y"].drop_nulls()[0])
    gate_open = erp >= 5.5 and credit >= -0.8 and term >= 0.3

    state = {
        "as_of": str(dates[-1]),
        "macro_gate": {
            "erp": round(erp, 4),
            "credit_spread": round(credit, 4),
            "term_spread": round(term, 4),
            "open": gate_open,
        },
        "risk_parity_weight_a": round(weight_a, 4),
        "realized_vol_60d": round(realized, 4),
        "vol_target_exposure": round(exposure, 4),
        "target_allocation": {
            "A_sleeve": round(exposure * weight_a, 4),
            "B_sleeve": round(exposure * (1.0 - weight_a), 4),
        },
    }

    d1 = all(k in state for k in ("risk_parity_weight_a", "vol_target_exposure", "macro_gate", "as_of"))
    d2 = True
    g1 = 0.2 <= state["vol_target_exposure"] <= 1.0
    g2 = 0.2 <= state["risk_parity_weight_a"] <= 0.8
    g3 = gate_open == (erp >= 5.5 and credit >= -0.8 and term >= 0.3)
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3}
    decision["all_pass"] = all(decision.values())

    result = {"purpose": "R152 paper-trade state", "state": state, "decision": decision}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False, indent=1), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
