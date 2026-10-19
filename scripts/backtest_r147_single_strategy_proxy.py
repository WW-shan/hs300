#!/usr/bin/env python3
"""R147: single-strategy proxy for the dual-sleeve candidate (for UI use)."""
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
OUT = EVIDENCE / "r147_single_strategy_proxy.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R144_SCRIPT = ROOT / "scripts" / "backtest_r144_portfolio_sensitivity.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R147_CACHE = DATA / "research" / "r147_cache"
PROXY = "ai_hs300_dual_sleeve_proxy"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r144 = _load(R144_SCRIPT, "r144")
    r127 = _load(R127_SCRIPT, "r127")
    R147_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    records = []
    per_phase = {}
    recent = None
    full = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R147_CACHE / f"{PROXY}_{key}_{r112.END.isoformat()}.parquet"
        if path.exists():
            curve = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        else:
            cfg = r112.StrategyBacktestConfig(
                strategy_id=PROXY, symbols=None, start=phase, end=r112.END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                max_positions=10, position_sizing="equal", mode="position",
                asset_type="stock", hs300=True,
            )
            with r112._patched_membership(membership):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{PROXY} {phase}: {result.error}")
            curve = r112._curve_frame(result.equity_curve)
            curve.write_parquet(path)
            print(f"ran {PROXY} {phase}: ret={result.stats.get('total_return')}", flush=True)
        per_phase[key] = r112._stats(curve)
        records.extend(r127._window_stats(curve["value"].to_list()))
        if key == "2025-10-14":
            recent = per_phase[key]
        if key == "2021-06-01":
            full = per_phase[key]

    summary = r127._summarize(records)
    d1 = len(per_phase) == 15
    g1 = summary["positive_rate"] >= 0.80
    g2 = summary["return_worst"] >= -0.25
    g3 = recent is not None and recent["total_return"] > 0
    g4 = full is not None and full["sharpe"] >= 0.6
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R147 single-strategy proxy for UI use",
        "strategy": PROXY,
        "rolling_1y": summary,
        "full_period": full,
        "recent_year": recent,
        "per_phase": per_phase,
        "decision": decision,
        "caveats": ["Proxy is not equivalent to the dual-sleeve + vol-target candidate."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("rolling:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("full:", json.dumps(full, ensure_ascii=False), flush=True)
    print("recent:", json.dumps(recent, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
