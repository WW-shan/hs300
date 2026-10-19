#!/usr/bin/env python3
"""R122: 20-day block bootstrap of 1-year paths for the PIT-industry candidate.

Candidate = PIT Shenwan industry B sleeve + 15% volatility target (R119).
Baseline  = the same B sleeve without the volatility overlay.

Both use the same block start indices so the comparison is paired.
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
OUT = EVIDENCE / "r122_bootstrap_1y.json"
R119_CACHE = DATA / "research" / "r119_cache"
PHASE = "2021-06-01"
BLOCK = 20
DAYS = 252
PATHS = 1000
SEED = 20261019


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _daily_returns(values: list[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _path_stats(returns: list[float]) -> dict:
    value = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns:
        value *= 1.0 + r
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(252.0) if sd > 0 else 0.0
    return {"total_return": value - 1.0, "mdd": mdd, "sharpe": sharpe}


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[index]


def _summarize(records: list[dict]) -> dict:
    returns = [r["total_return"] for r in records]
    mdds = [r["mdd"] for r in records]
    sharpes = [r["sharpe"] for r in records]
    return {
        "positive_rate": round(sum(v > 0 for v in returns) / len(returns), 4),
        "return_p05": round(_quantile(returns, 0.05), 4),
        "return_p25": round(_quantile(returns, 0.25), 4),
        "return_median": round(st.median(returns), 4),
        "return_p75": round(_quantile(returns, 0.75), 4),
        "return_p95": round(_quantile(returns, 0.95), 4),
        "mdd_p05": round(_quantile(mdds, 0.05), 4),
        "mdd_median": round(st.median(mdds), 4),
        "sharpe_median": round(st.median(sharpes), 4),
        "sharpe_p05": round(_quantile(sharpes, 0.05), 4),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]
    path = R119_CACHE / f"{strategy_id}_{PHASE}_{r112.END.isoformat()}.parquet"
    if not path.exists():
        raise RuntimeError(f"missing R119 cache: {path}")
    b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
    overlay = r114._vol_target(b)

    base_returns = _daily_returns(b["value"].to_list())
    cand_returns = _daily_returns(overlay["curve"]["value"].to_list())
    n = min(len(base_returns), len(cand_returns))
    base_returns = base_returns[:n]
    cand_returns = cand_returns[:n]

    rng = random.Random(SEED)
    base_records = []
    cand_records = []
    for _ in range(PATHS):
        starts = [rng.randrange(n) for _ in range(DAYS // BLOCK)]
        base_path = []
        cand_path = []
        for start in starts:
            for offset in range(BLOCK):
                index = (start + offset) % n
                base_path.append(base_returns[index])
                cand_path.append(cand_returns[index])
        base_records.append(_path_stats(base_path))
        cand_records.append(_path_stats(cand_path))

    base = _summarize(base_records)
    cand = _summarize(cand_records)

    d1 = n >= 1300 and len(base_records) == PATHS and len(cand_records) == PATHS
    g1 = cand["positive_rate"] >= 0.85
    g2 = cand["return_p05"] >= -0.15
    g3 = cand["return_median"] >= 0.05
    g4 = cand["mdd_p05"] >= -0.35
    g5 = cand["sharpe_median"] >= base["sharpe_median"] + 0.15
    g6 = cand["mdd_p05"] >= base["mdd_p05"] + 0.08
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R122 20-day block bootstrap of 1-year paths (PIT-industry candidate)",
        "universe": "R119 PIT Shenwan industry B-sleeve cache",
        "method": {
            "block_days": BLOCK,
            "path_days": DAYS,
            "paths": PATHS,
            "seed": SEED,
            "paired": True,
            "source": f"{strategy_id} {PHASE}..{r112.END}",
            "return_days": n,
        },
        "baseline_B_pit_industry": base,
        "candidate_vol_target_15pct": cand,
        "decision": decision,
        "final_recommendation": (
            "1-year distribution supports forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Block bootstrap reuses the same historical path; it is not new out-of-sample data.",
            "The overlay was calibrated on this sample (R114/R115 follow-up), so the bootstrap cannot remove calibration bias.",
            "SW industry code first 2 digits is a proxy for L1, not official name.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("baseline", base, flush=True)
    print("candidate", cand, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())