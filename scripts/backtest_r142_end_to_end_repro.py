#!/usr/bin/env python3
"""R142: end-to-end reproduction check of the dual-sleeve candidate
against the R125/R112 caches after the R138 full ext_data rebuild."""
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
OUT = EVIDENCE / "r142_end_to_end_repro.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
R142_CACHE = DATA / "research" / "r142_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _compare(a: pl.DataFrame, b: pl.DataFrame) -> dict:
    joined = (
        a.rename({"value": "new"}).join(b.rename({"value": "old"}), on="date", how="inner").sort("date")
    )
    diff = (joined["new"] - joined["old"]).abs()
    return {"rows": joined.height, "max_abs_diff": float(diff.max()) if joined.height else None}


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15
    R142_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    def run(strategy_id: str, start: date, tag: str) -> pl.DataFrame:
        path = R142_CACHE / f"{strategy_id}_{start.isoformat()}_{r112.END.isoformat()}_{tag}.parquet"
        if path.exists():
            return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        cfg = r112.StrategyBacktestConfig(
            strategy_id=strategy_id, symbols=None, start=start, end=r112.END,
            params={}, overrides={}, matching="open_t+1",
            fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
            max_positions=10, position_sizing="equal", mode="position",
            asset_type="stock", hs300=True,
        )
        with r112._patched_membership(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"{strategy_id} {start}: {result.error}")
        frame = r112._curve_frame(result.equity_curve)
        frame.write_parquet(path)
        print(f"ran {strategy_id} {start}: days={frame.height}", flush=True)
        return frame

    a_full = run(A_SLEEVE, date(2021, 6, 1), "3x")
    a_recent = run(A_SLEEVE, date(2025, 10, 14), "3x")
    b_full = run(B_SLEEVE, date(2021, 6, 1), "3x")

    a_full_old = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_2021-06-01_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
    a_recent_old = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_2025-10-14_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
    b_full_old = pl.read_parquet(R112_CACHE / f"{B_SLEEVE}_2021-06-01_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))

    cmp_a_full = _compare(a_full, a_full_old)
    cmp_a_recent = _compare(a_recent, a_recent_old)
    cmp_b_full = _compare(b_full, b_full_old)

    # recompute the R126 rolling distribution with the fresh curves + R119 B for other phases
    R119_CACHE = DATA / "research" / "r119_cache"
    records = []
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        if key == "2021-06-01":
            a = a_full
            b = b_full
        else:
            a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
            b = pl.read_parquet(R112_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        cand = r114._vol_target(risk)["curve"]
        values = cand["value"].to_list()
        for start in range(0, len(values) - WINDOW + 1):
            w = values[start : start + WINDOW]
            records.append(w[-1] / w[0] - 1.0)
    positive_rate = round(sum(v > 0 for v in records) / len(records), 4)

    d1 = a_full.height > 1000 and a_recent.height > 200 and b_full.height > 1000
    g1 = cmp_a_full["max_abs_diff"] is not None and cmp_a_full["max_abs_diff"] < 1e-6
    g2 = cmp_a_recent["max_abs_diff"] is not None and cmp_a_recent["max_abs_diff"] < 1e-6
    g3 = cmp_b_full["max_abs_diff"] is not None and cmp_b_full["max_abs_diff"] < 1e-6
    g4 = abs(positive_rate - 0.8839) <= 0.005
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R142 end-to-end reproduction after the 64-field rebuild",
        "comparisons": {
            "A_full_2021-06": cmp_a_full,
            "A_recent_2025-10": cmp_a_recent,
            "B_full_2021-06": cmp_b_full,
        },
        "rolling_positive_rate": positive_rate,
        "r126_baseline": 0.8839,
        "decision": decision,
        "caveats": ["Only the longest phase curves are freshly rerun; the rest reuse caches."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["comparisons"], ensure_ascii=False), flush=True)
    print("rolling positive rate:", positive_rate, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
