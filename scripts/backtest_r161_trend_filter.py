#!/usr/bin/env python3
"""R161: index trend-filter overlay (HS300 below 200d MA -> de-risk)."""
from __future__ import annotations

import importlib.util
import json
import random
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r161_trend_filter.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R159_CACHE = DATA / "research" / "r159_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252
BLOCK = 20
PATHS = 2000
SEED = 20261020
COST = 0.0052
MULTIPLIERS = [1.0, 0.75, 0.5, 0.25]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _q(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))]


def _bootstrap(returns: list[float]) -> dict:
    rng = random.Random(SEED)
    n_blocks = (WINDOW + BLOCK - 1) // BLOCK
    max_start = len(returns) - BLOCK
    paths = []
    for _ in range(PATHS):
        path = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start)
            path.extend(returns[s : s + BLOCK])
        val = 1.0
        for r in path[:WINDOW]:
            val *= 1.0 + r
        paths.append(val - 1.0)
    return {
        "positive_rate": round(sum(v > 0 for v in paths) / len(paths), 4),
        "p05": round(_q(paths, 0.05), 4),
        "median": round(st.median(paths), 4),
        "worst": round(min(paths), 4),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(R159_CACHE / f"{A_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    b = pl.read_parquet(R159_CACHE / f"{B_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    risk, _ = r112._combine(a, b, "risk")
    base = r114._vol_target(risk)["curve"].sort("date")

    idx = (
        pl.scan_parquet([str(p) for p in sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
        .with_columns(pl.col("close").rolling_mean(200).alias("ma200"))
        .with_columns((pl.col("close") >= pl.col("ma200")).alias("trend_on"))
    )

    joined = base.join(idx, on="date", how="left").sort("date")
    dates = joined["date"].to_list()
    values = joined["value"].to_list()
    trend = joined["trend_on"].to_list()
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]

    results = {}
    for m in MULTIPLIERS:
        portfolio = 1.0
        prev_m = 1.0
        out = [1.0]
        turnover_total = 0.0
        for i in range(1, len(dates)):
            on = trend[i - 1]
            cur_m = 1.0 if (on is None or on) else m
            if cur_m != prev_m:
                turnover_total += abs(cur_m - prev_m)
                portfolio *= 1.0 - abs(cur_m - prev_m) * COST
                prev_m = cur_m
            portfolio *= 1.0 + cur_m * rets[i - 1]
            out.append(portfolio)
        windows = [out[i + WINDOW] / out[i] - 1.0 for i in range(len(out) - WINDOW)]
        summary = {
            "positive_rate": round(sum(v > 0 for v in windows) / len(windows), 4),
            "p05": round(_q(windows, 0.05), 4),
            "median": round(st.median(windows), 4),
            "worst": round(min(windows), 4),
            "total_return": round(out[-1] - 1.0, 4),
            "turnover": round(turnover_total, 3),
            "bootstrap": _bootstrap([out[i] / out[i - 1] - 1.0 for i in range(1, len(out))]),
        }
        results[f"m={m}"] = summary
        print(f"m={m}: {json.dumps(summary, ensure_ascii=False)}", flush=True)

    baseline = results["m=1.0"]
    best_label = max((k for k in results if k != "m=1.0"), key=lambda k: results[k]["positive_rate"])
    best = results[best_label]

    d1 = abs(baseline["positive_rate"] - 0.7573) <= 0.002
    g1 = best["positive_rate"] >= baseline["positive_rate"] + 0.03
    g2 = best["bootstrap"]["p05"] >= baseline["bootstrap"]["p05"] + 0.02
    g3 = best["worst"] >= baseline["worst"]
    g4 = best["median"] >= baseline["median"] - 0.02
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R161 index trend-filter overlay",
        "results": results,
        "best": best_label,
        "decision": decision,
        "caveats": ["Daily trend check; 200d MA has a warmup."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("best:", best_label, json.dumps(best, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
