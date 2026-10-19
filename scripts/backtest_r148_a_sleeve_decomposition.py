#!/usr/bin/env python3
"""R148: decompose the A sleeve into gate vs scoring contributions."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r148_a_sleeve_decomposition.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R148_CACHE = DATA / "research" / "r148_cache"
VARIANTS = {
    "V1_gate_allpool": "ai_hs300_gate_allpool",
    "V2_quality_no_gate": "ai_hs300_quality_no_gate",
    "V3_gate_quality_current": "ai_hs300_macro_gate_quality",
    "V4_gate_lowvol": "ai_hs300_gate_lowvol",
    "V5_gate_quality_no_holder": "ai_hs300_gate_quality_no_holder",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r127 = _load(R127_SCRIPT, "r127")
    R148_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    results = {}
    for label, sid in VARIANTS.items():
        records = []
        for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
            key = phase.isoformat()
            path = R148_CACHE / f"{sid}_{key}_{r112.END.isoformat()}.parquet"
            if path.exists():
                curve = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
            else:
                cfg = r112.StrategyBacktestConfig(
                    strategy_id=sid, symbols=None, start=phase, end=r112.END,
                    params={}, overrides={}, matching="open_t+1",
                    fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                    max_positions=10, position_sizing="equal", mode="position",
                    asset_type="stock", hs300=True,
                )
                with r112._patched_membership(membership):
                    result = service.run(cfg)
                if result.error:
                    raise RuntimeError(f"{sid} {phase}: {result.error}")
                curve = r112._curve_frame(result.equity_curve)
                curve.write_parquet(path)
            records.extend(r127._window_stats(curve["value"].to_list()))
        summary = r127._summarize(records)
        results[label] = summary
        print(f"{label}: {json.dumps(summary, ensure_ascii=False)}", flush=True)

    v3 = results["V3_gate_quality_current"]
    d1 = len(results) == 5 and all(v["windows"] >= 300 for v in results.values())
    g1 = all(v3["positive_rate"] >= v["positive_rate"] for v in results.values())
    g2 = results["V1_gate_allpool"]["positive_rate"] >= 0.70
    g3 = results["V2_quality_no_gate"]["positive_rate"] < v3["positive_rate"]
    g4 = results["V5_gate_quality_no_holder"]["positive_rate"] >= v3["positive_rate"] - 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R148 A-sleeve decomposition: gate vs scoring",
        "variants": VARIANTS,
        "results": results,
        "decision": decision,
        "caveats": ["Windows overlap; descriptive only."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
