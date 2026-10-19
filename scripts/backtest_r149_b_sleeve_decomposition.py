#!/usr/bin/env python3
"""R149: decompose the B sleeve into components, stop-loss and holding period."""
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
OUT = EVIDENCE / "r149_b_sleeve_decomposition.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R149_CACHE = DATA / "research" / "r149_cache"
VARIANTS = {
    "V1_rel_only": "ai_hs300_b_decomp_rel_only",
    "V2_indmom_only": "ai_hs300_b_decomp_indmom_only",
    "V3_quality_only": "ai_hs300_b_decomp_quality_only",
    "V4_current": "ai_hs300_industry_relative_strength_quality_h20_sl20",
    "V5_no_stop": "ai_hs300_b_decomp_no_stop",
    "V6_hold10": "ai_hs300_b_decomp_hold10",
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
    R149_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    results = {}
    for label, sid in VARIANTS.items():
        records = []
        for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
            key = phase.isoformat()
            path = R149_CACHE / f"{sid}_{key}_{r112.END.isoformat()}.parquet"
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

    v4 = results["V4_current"]
    d1 = len(results) == 6 and all(v["windows"] >= 300 for v in results.values())
    g1 = all(v4["positive_rate"] >= results[k]["positive_rate"] for k in ("V1_rel_only", "V2_indmom_only", "V3_quality_only"))
    g2 = v4["positive_rate"] >= results["V5_no_stop"]["positive_rate"]
    g3 = v4["positive_rate"] >= results["V6_hold10"]["positive_rate"]
    g4 = results["V1_rel_only"]["positive_rate"] >= 0.70
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R149 B-sleeve decomposition: components, stop-loss, holding period",
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
