#!/usr/bin/env python3
"""R118: current-membership sanity check for the 15% B volatility target."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.strategy import StrategyBacktestConfig

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
OUT = EVIDENCE / "r118_current_members_vol_target.json"
CACHE = DATA / "research" / "r118_current_members_cache"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


r112 = _load(R112_SCRIPT, "r112")
r114 = _load(R114_SCRIPT, "r114")
r114.TARGET_VOL = 0.15


def _cache_path(strategy_id: str, start: date, end: date) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}.parquet"


def _run_current_sleeve(service, *, strategy_id: str, start: date, end: date) -> pl.DataFrame:
    path = _cache_path(strategy_id, start, end)
    if path.exists():
        return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))

    cfg = StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
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
    result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id} {start}: {result.error}")
    frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    print(
        f"{strategy_id} {start}: days={frame.height} "
        f"ret={result.stats.get('total_return')} "
        f"elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return frame


def main() -> int:
    service = r112._service()
    strategy_id = r112.SLEEVES["B_industry_relative_strength_h20_sl20"]

    phase_outputs = {}
    recent_outputs = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        b = _run_current_sleeve(service, strategy_id=strategy_id, start=phase, end=r112.END)
        overlay = r114._vol_target(b)
        rec = {
            "phase": key,
            "B_current_members": r112._stats(b),
            "B_current_members_vol_target_15pct": r112._stats(overlay["curve"]),
            "annual_turnover": round(overlay["annual_turnover"], 4),
            "weight_mean": round(st.mean(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_min": round(min(row["weight"] for row in overlay["weight_records"]), 4),
            "weight_max": round(max(row["weight"] for row in overlay["weight_records"]), 4),
        }
        if phase in r112.PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec
        print(
            f"[{key}] B={rec['B_current_members']['total_return']:+.3f} "
            f"vol15={rec['B_current_members_vol_target_15pct']['total_return']:+.3f} "
            f"turnover={rec['annual_turnover']:.2f}",
            flush=True,
        )

    names = (
        "B_current_members",
        "B_current_members_vol_target_15pct",
    )
    long = {
        name: r112._aggregate([phase_outputs[key][name] for key in sorted(phase_outputs)])
        for name in names
    }
    recent = {
        name: r112._aggregate([recent_outputs[key][name] for key in sorted(recent_outputs)])
        for name in names
    }
    annual_turnover_mean = round(
        st.mean(phase_outputs[key]["annual_turnover"] for key in phase_outputs), 4
    )
    weight_mean = round(
        st.mean(phase_outputs[key]["weight_mean"] for key in phase_outputs), 4
    )
    weight_min = round(
        min(phase_outputs[key]["weight_min"] for key in phase_outputs), 4
    )
    weight_max = round(
        max(phase_outputs[key]["weight_max"] for key in phase_outputs), 4
    )

    overlay = long["B_current_members_vol_target_15pct"]
    base = long["B_current_members"]
    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    e1 = bool(
        overlay["annual_mean"] >= 0.15
        and overlay["sharpe_mean"] >= 0.90
        and overlay["mdd_mean"] >= -0.225
        and overlay["mdd_worst"] >= -0.30
    )
    e2 = overlay["positive_phases"] == 10
    e3 = bool(
        recent["B_current_members_vol_target_15pct"]["total_return_mean"] > 0
        and recent["B_current_members_vol_target_15pct"]["positive_phases"] >= 4
    )
    e4 = bool(
        overlay["sharpe_mean"] >= base["sharpe_mean"] + 0.15
        and overlay["annual_mean"] >= base["annual_mean"] * 0.60
        and overlay["mdd_mean"] >= base["mdd_mean"] + 0.15
    )
    e5 = bool(0.30 <= weight_mean <= 1.00)
    e6 = annual_turnover_mean <= 1.5
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
        "E5": e5,
        "E6": e6,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R118 current-membership sanity check for the 15% B volatility target",
        "universe": "current HS300 membership applied to the full history (survivorship bias)",
        "window": {"start": str(r112.START), "end": str(r112.END), "phases": [str(x) for x in r112.PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in r112.RECENT_STARTS],
        "sleeve": strategy_id,
        "rule": {
            "lookback_days": 60,
            "target_annual_volatility": 0.15,
            "weight_bounds": [0.2, 1.0],
            "rebalance": "monthly",
            "round_trip_cost": 0.0052,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "annual_turnover_mean": annual_turnover_mean,
        "weight": {
            "mean": weight_mean,
            "min": weight_min,
            "max": weight_max,
        },
        "decision": decision,
        "final_recommendation": (
            "15% volatility target passes the current-membership sanity check"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "Current membership is backfilled over history and has survivorship bias.",
            "This run does not replace the historical-membership evidence from R115/R116.",
            "No production approval is implied by this evidence.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long overlay", long["B_current_members_vol_target_15pct"], flush=True)
    print("recent overlay", recent["B_current_members_vol_target_15pct"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())