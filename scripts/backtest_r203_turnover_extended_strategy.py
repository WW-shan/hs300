#!/usr/bin/env python3
"""R203: system-engine backtest for the 2017-extended turnover gate."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r203_turnover_history_extended.json"
SCAN = EVIDENCE / "r203_turnover_scan.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
CACHE = DATA / "research" / "r203_cache"
START = date(2018, 1, 2)
END = date(2026, 10, 16)
WINDOW = 252
STRATEGIES = {
    "extended_2017": "ai_hs300_turnover_level_gate_ext2017",
    "baseline_r201": "ai_hs300_turnover_level_gate",
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


def _benchmark_dates() -> list[date]:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter((pl.col("symbol") == "000300.SH") & (pl.col("date") >= START) & (pl.col("date") <= END))
        .select("date")
        .collect()
        .sort("date")["date"]
        .to_list()
    )


def _run(service, membership: pl.DataFrame, r112, sid: str) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sid}_{START.isoformat()}_{END.isoformat()}.parquet"
    if path.exists() and path.stat().st_size:
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}
    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=START, end=END,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=300, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "???????" in str(result.error):
            frame = pl.DataFrame({"date": _benchmark_dates()}).with_columns(pl.lit(1_000_000.0).alias("value"))
        else:
            raise RuntimeError(f"{sid}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    return frame, {"cached": False, "days": frame.height, "curve_stats": r112._stats(frame), "result_stats": result.stats}


def _segment(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        w = values[start:start + WINDOW]
        peak = w[0]
        mdd = 0.0
        for value in w:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        out.append({"return": w[-1] / w[0] - 1.0, "mdd": mdd})
    return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))]


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


def _returns(curve: pl.DataFrame) -> list[float]:
    values = curve["value"].to_list()
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _compound(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def _mdd(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    membership = _membership()
    service = r112._service()
    curves: dict[str, pl.DataFrame] = {}
    runs: dict[str, dict] = {}
    for name, sid in STRATEGIES.items():
        curve, info = _run(service, membership, r112, sid)
        curves[name] = curve
        runs[name] = info
        print(f"{name}: {info}", flush=True)

    scan = json.loads(SCAN.read_text(encoding="utf-8"))
    results = {}
    for name, curve in curves.items():
        rolling = _summarize(_window_stats(curve["value"].to_list()))
        results[name] = {
            "strategy_id": STRATEGIES[name],
            "full": r112._stats(curve),
            "independent_2018_2020": r112._stats(_segment(curve, date(2018, 1, 1), date(2020, 12, 31))),
            "validation_2021_2024": r112._stats(_segment(curve, date(2021, 1, 1), date(2024, 12, 31))),
            "holdout_2025_2026": r112._stats(_segment(curve, date(2025, 1, 1), END)),
            "rolling_1y": rolling,
            "recent_12m": {
                "total_return": round(_compound(_returns(curve)[-252:]), 6) if len(_returns(curve)) >= 252 else None,
                "mdd": round(_mdd(_returns(curve)[-252:]), 6) if len(_returns(curve)) >= 252 else None,
            },
        }

    ext = results["extended_2017"]
    base = results["baseline_r201"]
    d1 = (
        scan["membership"]["snapshots"] == 12
        and scan["daily_2017"]["coverage"] >= 0.95
        and scan["signal_coverage_2018_2026"]["coverage"] >= 0.90
    )
    records = scan["monthly_records"]
    signal_records = [r for r in records if r["hs300_turnover_z_ext2017"] is not None]
    d2 = (
        len(records) >= 12
        and bool(signal_records)
        and signal_records[0]["date"] >= records[11]["date"]
        and all(records[i]["date"] < records[i + 1]["date"] for i in range(len(records) - 1))
        and records[-1]["date"] <= END.isoformat()
        and scan["membership"]["update_after_snapshot"] == 0
    )
    g1 = ext["independent_2018_2020"]["total_return"] >= base["independent_2018_2020"]["total_return"] + 0.05
    g2 = ext["independent_2018_2020"]["mdd"] >= base["independent_2018_2020"]["mdd"] - 0.02
    g3 = ext["rolling_1y"]["positive_rate"] >= base["rolling_1y"]["positive_rate"]
    g4 = ext["full"]["mdd"] >= base["full"]["mdd"] - 0.02
    g5 = ext["full"]["total_return"] >= base["full"]["total_return"] - 0.05
    g6 = (
        ext["recent_12m"]["total_return"] is not None
        and ext["recent_12m"]["total_return"] > 0
        and ext["recent_12m"]["mdd"] >= base["recent_12m"]["mdd"] - 0.02
    )
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R203 system-engine test of 2017-extended turnover gate",
        "window": [str(START), str(END)],
        "execution": {
            "matching": "open_t+1",
            "cost": "3x: 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 300,
            "holding_days": 20,
            "universe": "historical HS300 membership; monthly 2018-2020, daily 2020-2026",
        },
        "data_check": {
            "membership_snapshots_2017": scan["membership"]["snapshots"],
            "daily_2017_coverage": scan["daily_2017"]["coverage"],
            "signal_coverage_2018_2026": scan["signal_coverage_2018_2026"]["coverage"],
        },
        "runs": runs,
        "results": results,
        "decision": decision,
        "caveats": [
            "The extended factor changes only the warmup history; the z-score formula and threshold are unchanged.",
            "The 2017 membership snapshots are monthly; intra-month index changes are not modeled.",
            "The strategy is an experimental equal-weight basket, not the R131 dual-sleeve portfolio.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"data_check": result["data_check"], "results": results, "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
