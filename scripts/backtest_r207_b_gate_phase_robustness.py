#!/usr/bin/env python3
"""R207: fresh-start phase robustness for the R204 turnover-gated B sleeve."""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r207_b_gate_phase_robustness.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
CACHE = DATA / "research" / "r207_cache"
STRATEGIES = {
    "gated": "ai_hs300_industry_relative_strength_quality_h20_sl20_turnover_gate",
    "base": "ai_hs300_industry_relative_strength_quality_h20_sl20",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _membership() -> pl.DataFrame:
    old = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    return (
        pl.concat([old, new], how="diagonal_relaxed")
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


def _benchmark_dates(start: date, end: date) -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter((pl.col("symbol") == "000300.SH") & (pl.col("date") >= start) & (pl.col("date") <= end))
        .select("date")
        .collect()
        .sort("date")["date"]
        .to_list()
    )


def _gate_open_in_range(start: date, end: date) -> bool:
    root = DATA / "ext_data" / "alt_hs300" / "timeseries"
    for path in sorted(root.glob("date=*/part.parquet")):
        day = date.fromisoformat(path.parent.name.split("=")[1])
        if day < start or day > end:
            continue
        frame = pl.read_parquet(path, columns=["hs300_turnover_gate_flag_ext2017"])
        if frame.height and frame["hs300_turnover_gate_flag_ext2017"].max() > 0:
            return True
    return False


def _run(service, membership: pl.DataFrame, r112, sid: str, start: date, end: date) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sid}_{start.isoformat()}_{end.isoformat()}.parquet"
    if path.exists() and path.stat().st_size:
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}
    if sid == STRATEGIES["gated"] and not _gate_open_in_range(start, end):
        frame = pl.DataFrame({"date": _benchmark_dates(start, end)}).with_columns(pl.lit(1_000_000.0).alias("value"))
        frame.write_parquet(path)
        return frame, {"cached": False, "cash_only": True, "reason": "turnover gate closed for entire phase", "days": frame.height, "curve_stats": r112._stats(frame)}
    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "\u672a\u4ea7\u751f\u4e70\u5165\u4fe1\u53f7" in str(result.error):
            frame = pl.DataFrame({"date": _benchmark_dates(start, end)}).with_columns(pl.lit(1_000_000.0).alias("value"))
        else:
            raise RuntimeError(f"{sid} {start}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    return frame, {"cached": False, "days": frame.height, "curve_stats": r112._stats(frame), "result_stats": result.stats}


def _summary(records: list[dict]) -> dict:
    returns = [float(r["total_return"]) for r in records]
    mdds = [float(r["mdd"]) for r in records]
    sharpes = [float(r["sharpe"]) for r in records]
    return {
        "phases": len(records),
        "positive_phases": sum(v > 0 for v in returns),
        "mean_return": round(st.mean(returns), 6),
        "median_return": round(st.median(returns), 6),
        "worst_return": round(min(returns), 6),
        "best_return": round(max(returns), 6),
        "mean_mdd": round(st.mean(mdds), 6),
        "worst_mdd": round(min(mdds), 6),
        "mean_sharpe": round(st.mean(sharpes), 4),
        "median_sharpe": round(st.median(sharpes), 4),
    }


def _run_single(strategy_name: str, start_text: str) -> int:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy_name}")
    r112 = _load(R112_SCRIPT, "r112")
    membership = _membership()
    service = r112._service()
    start = date.fromisoformat(start_text)
    curve, info = _run(service, membership, r112, STRATEGIES[strategy_name], start, r112.END)
    print(f"{strategy_name} {start}: {info}", flush=True)
    return 0


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    membership = _membership()
    service = r112._service()
    phases = list(r112.PHASE_STARTS) + list(r112.RECENT_STARTS)
    end = r112.END
    results = {}
    runs = {}
    for name, sid in STRATEGIES.items():
        phase_results = []
        phase_runs = []
        for phase in phases:
            curve, info = _run(service, membership, r112, sid, phase, end)
            stats = r112._stats(curve)
            phase_results.append({"start": str(phase), **stats})
            phase_runs.append({"start": str(phase), **info})
            print(f"{name} {phase}: ret={stats['total_return']:+.4f} mdd={stats['mdd']:+.4f}", flush=True)
        results[name] = {
            "phase_results": phase_results,
            "all": _summary(phase_results),
            "recent_5": _summary(phase_results[-5:]),
        }
        runs[name] = phase_runs

    gated = results["gated"]
    base = results["base"]
    d1 = len(runs["gated"]) == 15 and len(runs["base"]) == 15 and all(r["days"] > 0 for r in runs["gated"] + runs["base"])
    g1 = gated["all"]["positive_phases"] >= base["all"]["positive_phases"]
    g2 = gated["all"]["mean_return"] >= base["all"]["mean_return"] - 0.02
    g3 = gated["all"]["worst_mdd"] >= base["all"]["worst_mdd"] - 0.02
    g4 = gated["all"]["mean_sharpe"] >= base["all"]["mean_sharpe"]
    g5 = gated["recent_5"]["mean_return"] >= base["recent_5"]["mean_return"] - 0.02
    g6 = gated["recent_5"]["positive_phases"] >= base["recent_5"]["positive_phases"]
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R207 fresh-start phase robustness for the turnover-gated B sleeve",
        "window_end": str(end),
        "phase_starts": [str(p) for p in phases],
        "execution": {
            "matching": "open_t+1",
            "cost": "3x: 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 10,
            "holding_days": 20,
            "stop_loss": -0.20,
        },
        "runs": runs,
        "results": results,
        "decision": decision,
        "caveats": [
            "Fresh-start phases are independent runs, not a continuous portfolio.",
            "The last 5 phases are recent-start diagnostics; they overlap heavily.",
            "This round does not change the R204 factor or strategy definitions.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"results": results, "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--single-strategy", choices=sorted(STRATEGIES))
    parser.add_argument("--single-start")
    args = parser.parse_args()
    if args.single_strategy or args.single_start:
        if not (args.single_strategy and args.single_start):
            raise SystemExit("--single-strategy and --single-start must be used together")
        raise SystemExit(_run_single(args.single_strategy, args.single_start))
    raise SystemExit(main())
