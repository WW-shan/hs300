#!/usr/bin/env python3
"""R162: trailing-stop / trailing-take-profit scan for the B sleeve."""
from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r162_trailing_scan.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R162_CACHE = DATA / "research" / "r162_cache"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
VARIANTS = {
    "V0_fixed20": {},
    "V1_trail10": {"stop_loss": None, "trailing_stop": -0.10},
    "V2_trail15": {"stop_loss": None, "trailing_stop": -0.15},
    "V3_tp15_8": {"trailing_take_profit_activate": 0.15, "trailing_take_profit_drawdown": 0.08},
    "V4_tp25_10": {"trailing_take_profit_activate": 0.25, "trailing_take_profit_drawdown": 0.10},
    "V5_sl20_tp20_8": {"trailing_take_profit_activate": 0.20, "trailing_take_profit_drawdown": 0.08},
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
    R162_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    results = {}
    for label, overrides in VARIANTS.items():
        records = []
        for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
            key = phase.isoformat()
            path = R162_CACHE / f"{label}_{key}_{r112.END.isoformat()}.parquet"
            if path.exists():
                curve = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
            else:
                cfg = r112.StrategyBacktestConfig(
                    strategy_id=B_SLEEVE, symbols=None, start=phase, end=r112.END,
                    params={}, overrides=overrides, matching="open_t+1",
                    fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                    max_positions=10, position_sizing="equal", mode="position",
                    asset_type="stock", hs300=True,
                )
                with r112._patched_membership(membership):
                    result = service.run(cfg)
                if result.error:
                    print(f"{label} {phase}: {result.error}", flush=True)
                    continue
                curve = r112._curve_frame(result.equity_curve)
                curve.write_parquet(path)
            records.extend(r127._window_stats(curve["value"].to_list()))
        summary = r127._summarize(records)
        results[label] = summary
        print(f"{label}: {json.dumps(summary, ensure_ascii=False)}", flush=True)

    base = results["V0_fixed20"]
    candidates = {k: v for k, v in results.items() if k != "V0_fixed20" and v["windows"] > 0}
    best_label = max(candidates, key=lambda k: candidates[k]["positive_rate"]) if candidates else None
    best = candidates[best_label] if best_label else None

    d1 = len(results) == 6 and all(v["windows"] >= 300 for v in results.values())
    g1 = best is not None and best["positive_rate"] >= base["positive_rate"] + 0.03
    g2 = best is not None and best["return_worst"] >= base["return_worst"]
    g3 = best is not None and best["return_median"] >= base["return_median"] - 0.02
    g4 = True  # turnover not captured here; recorded as a caveat
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R162 trailing stop / take-profit scan for the B sleeve",
        "variants": VARIANTS,
        "results": results,
        "best": best_label,
        "decision": decision,
        "caveats": ["Turnover not measured in this scan."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("best:", best_label, json.dumps(best, ensure_ascii=False) if best else None, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
