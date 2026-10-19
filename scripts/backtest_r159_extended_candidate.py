#!/usr/bin/env python3
"""R159: full dual-sleeve candidate extended to 2020-06-01.

Uses the R157 extended membership (2018-2020 monthly + daily afterwards).
The A sleeve becomes viable once holder coverage is high (2020-06 onward).
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r159_extended_candidate.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R159_CACHE = DATA / "research" / "r159_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2020, 6, 1)
END = date(2026, 10, 16)
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15
    R159_CACHE.mkdir(parents=True, exist_ok=True)

    from app.backtest import strategy as strategy_module

    old = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    membership = (
        pl.concat([new, old])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )

    @contextmanager
    def patched(m):
        orig = strategy_module._hs300_membership_frame
        strategy_module._hs300_membership_frame = lambda _s, _e: m
        try:
            yield
        finally:
            strategy_module._hs300_membership_frame = orig

    service = r112._service()
    curves = {}
    for sid in (A_SLEEVE, B_SLEEVE):
        path = R159_CACHE / f"{sid}_{START.isoformat()}_{END.isoformat()}.parquet"
        if path.exists():
            curves[sid] = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
            continue
        cfg = r112.StrategyBacktestConfig(
            strategy_id=sid, symbols=None, start=START, end=END,
            params={}, overrides={}, matching="open_t+1",
            fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
            max_positions=10, position_sizing="equal", mode="position",
            asset_type="stock", hs300=True,
        )
        with patched(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"{sid}: {result.error}")
        frame = r112._curve_frame(result.equity_curve)
        frame.write_parquet(path)
        curves[sid] = frame
        print(f"ran {sid}: days={frame.height} ret={result.stats.get('total_return')}", flush=True)

    a, b = curves[A_SLEEVE], curves[B_SLEEVE]
    risk, _ = r112._combine(a, b, "risk")
    cand = r114._vol_target(risk)["curve"]

    # new-sample segment 2020-06..2021-06
    seg = cand.filter((pl.col("date") >= START) & (pl.col("date") <= date(2021, 6, 1))).sort("date")
    seg_stats = r112._stats(seg)

    # full rolling distribution
    records = r127._window_stats(cand["value"].to_list())
    summary = r127._summarize(records)

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = cand.height > 1400 and seg.height >= 240
    g1 = seg_stats["total_return"] > 0
    g2 = seg_stats["mdd"] >= -0.15
    g3 = summary["positive_rate"] >= 0.85
    g4 = summary["return_worst"] >= -0.15
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R159 full candidate extended to 2020-06",
        "window": [str(START), str(END)],
        "new_sample_2020_06_to_2021_06": seg_stats,
        "rolling_1y": summary,
        "reference_r131": base,
        "decision": decision,
        "caveats": [
            "Membership is monthly before 2020-12-31, daily afterwards.",
            "Holder coverage is 89% at 2020-06.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("new sample:", json.dumps(seg_stats, ensure_ascii=False), flush=True)
    print("rolling:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
