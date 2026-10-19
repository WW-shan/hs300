#!/usr/bin/env python3
"""R123: correct 1-year bootstrap with the volatility overlay re-applied per path.

R122 resampled overlay-adjusted returns directly, which ignores the position
feedback.  This script resamples the raw B-sleeve returns and re-applies the
15% volatility target inside every synthetic path.
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
OUT = EVIDENCE / "r123_bootstrap_1y_correct.json"
R119_CACHE = DATA / "research" / "r119_cache"
PHASE = "2021-06-01"
BLOCK = 20
DAYS = 252
PATHS = 1000
SEED = 20261019
TARGET_VOL = 0.15
MIN_WEIGHT = 0.20
MAX_WEIGHT = 1.00
ROUND_TRIP_COST = 0.0052


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def _apply_overlay(returns: list[float]) -> list[float]:
    """Re-apply the monthly 15% vol target inside a synthetic path."""
    out: list[float] = []
    sleeve = 1.0
    target = 0.5
    # Month boundary on the synthetic calendar: every 21 trading days.
    for i, ret in enumerate(returns):
        sleeve *= 1.0 + ret
        out.append(target * ret)
        if (i + 1) % 21 == 0:
            total_value = target * sleeve + (1.0 - target)
            actual_weight = target * sleeve / total_value if total_value else target
            if i + 1 < 60:
                next_target = 0.5
            else:
                window = returns[i - 59:i + 1]
                realized_vol = st.pstdev(window) * math.sqrt(252.0)
                if realized_vol <= TARGET_VOL:
                    next_target = MAX_WEIGHT
                else:
                    next_target = min(MAX_WEIGHT, max(MIN_WEIGHT, TARGET_VOL / realized_vol))
            turnover = abs(next_target - actual_weight)
            out[-1] -= turnover * ROUND_TRIP_COST
            sleeve = 1.0
            target = next_target
    return out


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
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]
    path = R119_CACHE / f"{strategy_id}_{PHASE}_{r112.END.isoformat()}.parquet"
    if not path.exists():
        raise RuntimeError(f"missing R119 cache: {path}")
    b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
    values = b["value"].to_list()
    base_returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    n = len(base_returns)

    rng = random.Random(SEED)
    base_records = []
    cand_records = []
    for _ in range(PATHS):
        starts = [rng.randrange(n) for _ in range(DAYS // BLOCK)]
        path_returns = []
        for start in starts:
            for offset in range(BLOCK):
                path_returns.append(base_returns[(start + offset) % n])
        base_records.append(_path_stats(path_returns))
        cand_records.append(_path_stats(_apply_overlay(path_returns)))

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

    r122_path = EVIDENCE / "r122_bootstrap_1y.json"
    r122 = json.loads(r122_path.read_text(encoding="utf-8")) if r122_path.exists() else None

    result = {
        "purpose": "R123 correct 1-year bootstrap with per-path volatility overlay",
        "universe": "R119 PIT Shenwan industry B-sleeve cache",
        "method": {
            "block_days": BLOCK,
            "path_days": DAYS,
            "paths": PATHS,
            "seed": SEED,
            "month_days": 21,
            "target_volatility": TARGET_VOL,
            "weight_bounds": [MIN_WEIGHT, MAX_WEIGHT],
            "round_trip_cost": ROUND_TRIP_COST,
            "return_days": n,
        },
        "baseline_B_pit_industry": base,
        "candidate_vol_target_15pct": cand,
        "r122_approximation": (
            {
                "baseline": r122["baseline_B_pit_industry"],
                "candidate": r122["candidate_vol_target_15pct"],
            }
            if r122
            else None
        ),
        "decision": decision,
        "final_recommendation": (
            "1-year distribution supports forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Block bootstrap reuses the same historical path; it is not new out-of-sample data.",
            "Synthetic months are 21 trading days; the real overlay uses calendar months.",
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