#!/usr/bin/env python3
"""R130: de-lever the dual-sleeve candidate when the macro gate is closed.

The gate is the same three-condition filter used by the A sleeve:
ERP >= 5.5, credit spread >= -0.8, term spread >= 0.3.  At each monthly
rebalance the target exposure is multiplied by m when the gate is closed
on the last trading day of the previous month.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r130_gate_deleveraging.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
WINDOW = 252
ROUND_TRIP_COST = 0.0052
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
MULTIPLIERS = [1.0, 0.75, 0.5, 0.25]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _gate_frame() -> pl.DataFrame:
    lazy = pl.scan_parquet(
        str(DATA / "ext_data" / "alt_hs300" / "timeseries" / "*" / "part.parquet"),
        hive_partitioning=True,
    )
    return (
        lazy.select(["date", "equity_risk_premium", "credit_spread_5y_low", "term_spread_10y_2y"])
        .collect()
        .unique(subset=["date"])
        .sort("date")
        .with_columns(
            (
                (pl.col("equity_risk_premium") >= 5.5)
                & (pl.col("credit_spread_5y_low") >= -0.8)
                & (pl.col("term_spread_10y_2y") >= 0.3)
            ).alias("gate_open")
        )
        .select(["date", "gate_open"])
    )


def _apply_gate(curve: pl.DataFrame, gate: pl.DataFrame, multiplier: float) -> pl.DataFrame:
    """Scale the vol-target curve by m during months after a closed-gate month end."""
    merged = curve.join(gate, on="date", how="left").sort("date")
    dates = merged["date"].to_list()
    values = merged["value"].to_list()
    opens = merged["gate_open"].to_list()

    # month-end gate state -> multiplier for the following month
    month_state = {}
    for i, day in enumerate(dates):
        is_month_end = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if is_month_end:
            month_state[(day.year, day.month)] = bool(opens[i]) if opens[i] is not None else True

    # For each calendar month, the applicable state is the gate state observed at
    # the last month-end strictly before that month (no look-ahead).
    months = sorted({(d.year, d.month) for d in dates})
    month_multiplier = {}
    prev_state = True
    for month in months:
        month_multiplier[month] = 1.0 if prev_state else multiplier
        if month in month_state:
            prev_state = month_state[month]

    out = [values[0]]
    for i in range(1, len(dates)):
        day = dates[i]
        m = month_multiplier[(day.year, day.month)]
        r = values[i] / values[i - 1] - 1.0
        out.append(out[-1] * (1.0 + m * r))
    return pl.DataFrame({"date": dates, "value": out})


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
    gate = _gate_frame()

    base_curves = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R112_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        base_curves[key] = r114._vol_target(risk)["curve"]

    results = {}
    for m in MULTIPLIERS:
        records = []
        for key, curve in base_curves.items():
            scaled = _apply_gate(curve, gate, m)
            records.extend(_window_stats(scaled["value"].to_list()))
        summary = _summarize(records)
        results[f"m={m}"] = summary
        print(f"m={m}: {json.dumps(summary, ensure_ascii=False)}", flush=True)

    base = results["m=1.0"]
    main = results["m=0.5"]
    d1 = abs(base["positive_rate"] - 0.8839) <= 0.001
    g1 = main["positive_rate"] >= base["positive_rate"] + 0.01
    g2 = main["return_worst"] >= base["return_worst"]
    g3 = main["return_median"] >= 0.10
    g4 = all(results[f"m={m}"]["positive_rate"] >= base["positive_rate"] - 0.02 for m in (0.75, 0.5, 0.25))
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R130 de-lever the dual-sleeve candidate when the macro gate is closed",
        "gate": "ERP >= 5.5, credit spread >= -0.8, term spread >= 0.3 (same as A sleeve filter)",
        "multipliers": MULTIPLIERS,
        "results": results,
        "decision": decision,
        "caveats": [
            "Windows overlap; descriptive only.",
            "The gate rule is motivated by R129 loss anatomy but still in-sample.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
