#!/usr/bin/env python3
"""R183: test whether the short-interest crowding tilt adds to the candidate.

Runs the new B-sleeve variant for all 15 phases (3x cost, historical
membership) and recomputes the dual-sleeve + 15% vol-target rolling
distribution against the R131 baseline.
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
OUT = EVIDENCE / "r183_short_tilt.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R183_CACHE = DATA / "research" / "r183_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_NEW = "ai_hs300_industry_relative_strength_quality_h20_sl20_short"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        w = values[start : start + WINDOW]
        peak = w[0]
        mdd = 0.0
        for v in w:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        out.append({"return": w[-1] / w[0] - 1.0, "mdd": mdd})
    return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[index]


def _summarize(records: list[dict]) -> dict:
    if not records:
        return {"windows": 0}
    returns = [r["return"] for r in records]
    mdds = [r["mdd"] for r in records]
    return {
        "windows": len(records),
        "positive_rate": round(sum(v > 0 for v in returns) / len(returns), 4),
        "return_p05": round(_quantile(returns, 0.05), 4),
        "return_median": round(st.median(returns), 4),
        "return_worst": round(min(returns), 4),
        "mdd_p05": round(_quantile(mdds, 0.05), 4),
        "mdd_worst": round(min(mdds), 4),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15
    R183_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    records = []
    b_stats = {}
    recent = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R183_CACHE / f"{B_NEW}_{key}_{r112.END.isoformat()}.parquet"
        if path.exists():
            b_new = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        else:
            cfg = r112.StrategyBacktestConfig(
                strategy_id=B_NEW, symbols=None, start=phase, end=r112.END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                max_positions=10, position_sizing="equal", mode="position",
                asset_type="stock", hs300=True,
            )
            with r112._patched_membership(membership):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{B_NEW} {phase}: {result.error}")
            b_new = r112._curve_frame(result.equity_curve)
            b_new.write_parquet(path)
            print(f"ran {B_NEW} {phase}: ret={result.stats.get('total_return')}", flush=True)
        b_stats[key] = r112._stats(b_new)
        a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b_new, "risk")
        cand = r114._vol_target(risk)["curve"]
        w = _window_stats(cand["value"].to_list())
        records.extend(w)
        if key == "2025-10-14":
            recent = r112._stats(cand)
        if w:
            print(f"[{key}] B_new={b_stats[key]['total_return']:+.3f} cand_pos="
                  f"{sum(1 for r in w if r['return'] > 0) / len(w):.3f}", flush=True)

    summary = _summarize(records)
    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = len(b_stats) == 15 and summary["windows"] >= 300
    g1 = st.mean(v["sharpe"] for v in b_stats.values()) >= 0.7296
    g2 = summary["positive_rate"] >= base["positive_rate"] + 0.01
    g3 = summary["return_worst"] >= base["return_worst"] - 0.01
    g4 = recent is not None and recent["total_return"] > 0
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R183 short-interest crowding tilt on the B sleeve",
        "strategy": B_NEW,
        "b_sleeve_stats": b_stats,
        "b_sleeve_sharpe_mean": round(st.mean(v["sharpe"] for v in b_stats.values()), 4),
        "rolling_1y": summary,
        "recent_year": recent,
        "reference_r131": base,
        "decision": decision,
        "caveats": ["The tilt weight (10%) is a single choice; no grid was run."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("B sharpe mean:", result["b_sleeve_sharpe_mean"], flush=True)
    print("rolling:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("recent:", json.dumps(recent, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
