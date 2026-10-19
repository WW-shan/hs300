#!/usr/bin/env python3
"""R137: full candidate validation for the ERP 6.0 gate variant.

Runs the A sleeve with erp_min=6.0 for all 15 phases plus the OOS start,
then evaluates the dual-sleeve + vol-target candidate exactly like R131/R132.
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
OUT = EVIDENCE / "r137_erp6_variant.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R119_CACHE = DATA / "research" / "r119_cache"
R137_CACHE = DATA / "research" / "r137_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252
OOS_START = date(2020, 12, 31)
OOS_END = date(2021, 6, 1)
PARAMS = {"erp_min": 6.0}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        window = values[start : start + WINDOW]
        peak = window[0]
        mdd = 0.0
        for v in window:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        out.append({"return": window[-1] / window[0] - 1.0, "mdd": mdd})
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
    R137_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()

    def run_a(start: date) -> pl.DataFrame:
        path = R137_CACHE / f"{A_SLEEVE}_{start.isoformat()}_{r112.END.isoformat()}_erp60.parquet"
        if path.exists():
            return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        cfg = r112.StrategyBacktestConfig(
            strategy_id=A_SLEEVE,
            symbols=None,
            start=start,
            end=r112.END,
            params=PARAMS,
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
        with r112._patched_membership(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"A erp60 {start}: {result.error}")
        frame = r112._curve_frame(result.equity_curve)
        frame.write_parquet(path)
        print(f"A erp60 {start}: days={frame.height} ret={result.stats.get('total_return')}", flush=True)
        return frame

    records = []
    per_phase = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = run_a(phase)
        b = pl.read_parquet(
            R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        overlay = r114._vol_target(risk)
        w = _window_stats(overlay["curve"]["value"].to_list())
        records.extend(w)
        per_phase[key] = _summarize(w)
        if w:
            print(f"[{key}] pos={per_phase[key]['positive_rate']} median={per_phase[key]['return_median']:+.3f}", flush=True)

    summary = _summarize(records)

    # recent year
    key_r = "2025-10-14"
    a_r = run_a(date(2025, 10, 14))
    b_r = pl.read_parquet(
        R119_CACHE / f"{B_SLEEVE}_{key_r}_{r112.END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_r, _ = r112._combine(a_r, b_r, "risk")
    cand_r = r114._vol_target(risk_r)["curve"]
    recent = r112._stats(cand_r)

    # OOS
    a_o = run_a(OOS_START)
    b_o = pl.read_parquet(
        DATA / "research" / "r132_cache" / f"{B_SLEEVE}_{OOS_START.isoformat()}_{r112.END.isoformat()}_pit.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_o, _ = r112._combine(a_o, b_o, "risk")
    cand_o = r114._vol_target(risk_o)["curve"]
    oos_curve = cand_o.filter((pl.col("date") >= OOS_START) & (pl.col("date") <= OOS_END)).sort("date")
    oos = r112._stats(oos_curve)

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]

    d1 = len(per_phase) == 15 and summary["windows"] >= 300 and recent["days"] > 200 and oos["days"] > 80
    g1 = summary["positive_rate"] >= base["positive_rate"]
    g2 = summary["return_worst"] >= base["return_worst"] - 0.02
    g3 = recent["total_return"] > 0 and recent["mdd"] >= -0.15
    g4 = oos["total_return"] > -0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R137 ERP 6.0 gate variant full candidate validation",
        "params": PARAMS,
        "rolling_1y": summary,
        "per_phase": per_phase,
        "recent": recent,
        "out_of_sample": oos,
        "reference_default_r131": base,
        "decision": decision,
        "caveats": [
            "ERP 6.0 was selected from the R136 grid, so selection bias remains.",
            "Windows overlap; descriptive only.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("rolling:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("recent:", json.dumps(recent, ensure_ascii=False), flush=True)
    print("oos:", json.dumps(oos, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
