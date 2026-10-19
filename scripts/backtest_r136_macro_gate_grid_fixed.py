#!/usr/bin/env python3
"""R136: rerun the macro-gate parameter grid on the fixed holder data.

R99's grid ran on the misaligned holder factor; this script reruns the same
18-point grid with the fixed ext_data at 3x cost and checks whether the
default parameters still sit on a robust plateau.
"""
from __future__ import annotations

import importlib.util
import json
import statistics as st
import time
from contextlib import contextmanager
from datetime import date
from itertools import product
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r136_macro_gate_grid_fixed.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
MACRO_STRATEGY = "ai_hs300_macro_gate_quality"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
GRID = {
    "erp_min": [5.5, 6.0],
    "credit_max": [0.6, 0.8, 1.0],
    "term_min": [0.2, 0.3, 0.4],
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    membership = r112._historical_membership()
    service = r112._service()

    records = []
    for erp, credit, term in product(GRID["erp_min"], GRID["credit_max"], GRID["term_min"]):
        params = {"erp_min": erp, "credit_max": credit, "term_min": term}
        cfg = r112.StrategyBacktestConfig(
            strategy_id=MACRO_STRATEGY,
            symbols=None,
            start=START,
            end=END,
            params=params,
            overrides={},
            matching="open_t+1",
            fees_pct=0.0006,
            commission_pct=0.0006,
            stamp_tax_pct=0.001,
            slippage_bps=15.0,
            max_positions=10,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        started = time.perf_counter()
        with r112._patched_membership(membership):
            result = service.run(cfg)
        stats = result.stats or {}
        record = {
            "params": params,
            "error": result.error,
            "total_return": stats.get("total_return"),
            "sharpe": stats.get("sharpe"),
            "mdd": stats.get("max_drawdown"),
            "n_trades": stats.get("n_trades"),
            "elapsed": round(time.perf_counter() - started, 2),
        }
        records.append(record)
        print(
            f"erp={erp} credit={credit} term={term}: "
            f"ret={record['total_return']} sharpe={record['sharpe']} mdd={record['mdd']}",
            flush=True,
        )

    ranked = sorted(records, key=lambda r: (r["sharpe"] is None, -(r["sharpe"] or -9)))
    default = next(
        r for r in records
        if r["params"] == {"erp_min": 5.5, "credit_max": 0.8, "term_min": 0.3}
    )
    default_rank = ranked.index(default) + 1

    neighborhood = [
        r for r in records
        if r["params"]["erp_min"] in (5.5, 6.0) and r["params"]["credit_max"] == 0.8
    ]
    plateau_count = sum(1 for r in neighborhood if (r["sharpe"] or -9) >= 0.75)

    d1 = all(r["error"] is None for r in records) and len(records) == 18
    g1 = (default["sharpe"] or -9) >= 0.80
    g2 = default_rank <= 5
    g3 = plateau_count >= 4
    g4 = (default["total_return"] or -9) >= 0.50
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R136 macro-gate grid on fixed holder data",
        "grid": GRID,
        "cost": "3x (6bp commission + 15bp slippage + 10bp stamp)",
        "records": records,
        "default": default,
        "default_rank": default_rank,
        "neighborhood_plateau_count": plateau_count,
        "decision": decision,
        "caveats": ["Grid is exploratory; no default change is implied."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("default:", json.dumps(default, ensure_ascii=False), "rank", default_rank, flush=True)
    print("plateau count:", plateau_count, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
