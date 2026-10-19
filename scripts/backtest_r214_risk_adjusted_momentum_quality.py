#!/usr/bin/env python3
"""R214: system-engine test of the HS300 risk-adjusted momentum quality strategy."""
from __future__ import annotations

import gc
import importlib.util
import json
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r214_risk_adjusted_momentum_quality.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
CACHE = DATA / "research" / "r214_cache"
START = date(2018, 1, 2)
END = date(2026, 10, 16)
WINDOW = 252
SEGMENTS = [
    (date(2018, 1, 2), date(2020, 12, 31)),
    (date(2021, 1, 4), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_risk_adjusted_momentum_quality_h20_sl20",
    "control": "ai_hs300_risk_adjusted_momentum_quality_control",
}
FACTOR_COLUMN = "risk_adjusted_momentum_60d"


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


def _run_segment(service, membership: pl.DataFrame, r112, sid: str, start: date, end: date) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sid}_{start.isoformat()}_{end.isoformat()}.parquet"
    if path.exists() and path.stat().st_size:
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}
    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "\u672a\u4ea7\u751f\u4e70\u5165\u4fe1\u53f7" in str(result.error):
            frame = pl.DataFrame({"date": _benchmark_dates(start, end)}).with_columns(pl.lit(1_000_000.0).alias("value"))
        else:
            raise RuntimeError(f"{sid} {start}~{end}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
        "factor_attribution": result.factor_attribution,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _stitch(segments: list[pl.DataFrame]) -> pl.DataFrame:
    rows: list[dict] = []
    equity = 1.0
    for curve in segments:
        values = curve["value"].to_list()
        dates = curve["date"].to_list()
        if not values:
            continue
        for i, day in enumerate(dates):
            if not rows and i == 0:
                rows.append({"date": day, "value": equity})
                continue
            if i == 0:
                continue
            equity *= values[i] / values[i - 1]
            rows.append({"date": day, "value": equity})
    return pl.DataFrame(rows).sort("date")


def _segment(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        window = values[start:start + WINDOW]
        peak = window[0]
        mdd = 0.0
        for value in window:
            peak = max(peak, value)
            mdd = min(mdd, value / peak - 1.0)
        out.append({"return": window[-1] / window[0] - 1.0, "mdd": mdd})
    return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))]


def _summarize(records: list[dict]) -> dict:
    if not records:
        return {"windows": 0}
    returns = [record["return"] for record in records]
    mdds = [record["mdd"] for record in records]
    return {
        "windows": len(records),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 4),
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


def _result(r112, curve: pl.DataFrame) -> dict:
    rolling = _summarize(_window_stats(curve["value"].to_list()))
    rets = _returns(curve)
    return {
        "full": r112._stats(curve),
        "independent_2018_2020": r112._stats(_segment(curve, date(2018, 1, 1), date(2020, 12, 31))),
        "validation_2021_2024": r112._stats(_segment(curve, date(2021, 1, 1), date(2024, 12, 31))),
        "holdout_2025_2026": r112._stats(_segment(curve, date(2025, 1, 1), END)),
        "rolling_1y": rolling,
        "recent_12m": {
            "total_return": round(_compound(rets[-WINDOW:]), 6) if len(rets) >= WINDOW else None,
            "mdd": round(_mdd(rets[-WINDOW:]), 6) if len(rets) >= WINDOW else None,
        },
    }


def _factor_coverage(membership: pl.DataFrame) -> dict:
    ext_root = DATA / "ext_data" / "alt_hs300" / "timeseries"
    factor = (
        pl.scan_parquet(str(ext_root / "date=*/part.parquet"), hive_partitioning=True)
        .filter(pl.col("date") <= END)
        .select(["symbol", "date", FACTOR_COLUMN])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
        .sort(["date", "symbol"])
    )
    dates = factor.select("date").unique().sort("date")["date"].to_list()
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")
    date_map = (
        pl.DataFrame({"date": dates})
        .sort("date")
        .join_asof(snapshot_dates, left_on="date", right_on="effective_date", strategy="backward")
    )
    members = date_map.join(membership, on="effective_date", how="inner").select(["date", "symbol"])
    eligible = members.join(factor, on=["date", "symbol"], how="left")
    recent_dates = dates[-WINDOW:]
    recent = eligible.filter(pl.col("date").is_in(recent_dates))
    first_valid = factor.filter(pl.col(FACTOR_COLUMN).is_not_null())["date"].min()
    return {
        "eligible_member_rows": eligible.height,
        "valid_member_rows": int(eligible[FACTOR_COLUMN].is_not_null().sum()),
        "coverage_2018_2026": round(float(eligible[FACTOR_COLUMN].is_not_null().sum()) / eligible.height, 6),
        "recent_member_rows": recent.height,
        "recent_valid_rows": int(recent[FACTOR_COLUMN].is_not_null().sum()),
        "recent_12m_coverage": round(float(recent[FACTOR_COLUMN].is_not_null().sum()) / recent.height, 6),
        "first_valid_date": str(first_valid),
        "required_trading_date": str(dates[60]),
        "factor_min": round(float(factor[FACTOR_COLUMN].min()), 6),
        "factor_median": round(float(factor[FACTOR_COLUMN].median()), 6),
        "factor_max": round(float(factor[FACTOR_COLUMN].max()), 6),
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    membership = _membership()
    service = r112._service()
    coverage = _factor_coverage(membership)

    curves: dict[str, pl.DataFrame] = {}
    runs: dict[str, list[dict]] = {}
    for name, sid in STRATEGIES.items():
        segment_curves = []
        segment_runs = []
        for start, end in SEGMENTS:
            curve, info = _run_segment(service, membership, r112, sid, start, end)
            segment_curves.append(curve)
            segment_runs.append({"start": str(start), "end": str(end), **info})
            print(f"{name} {start}~{end}: {info}", flush=True)
            gc.collect()
        curves[name] = _stitch(segment_curves)
        runs[name] = segment_runs

    results = {name: _result(r112, curve) for name, curve in curves.items()}
    candidate = results["candidate"]
    control = results["control"]

    d1 = coverage["coverage_2018_2026"] >= 0.90
    d2 = (
        coverage["first_valid_date"] >= coverage["required_trading_date"]
        and coverage["recent_12m_coverage"] >= 0.95
    )
    g1 = candidate["independent_2018_2020"]["total_return"] >= control["independent_2018_2020"]["total_return"] + 0.05
    g2 = candidate["independent_2018_2020"]["mdd"] >= control["independent_2018_2020"]["mdd"] - 0.02
    g3 = candidate["validation_2021_2024"]["total_return"] >= control["validation_2021_2024"]["total_return"] - 0.05
    g4 = candidate["rolling_1y"]["positive_rate"] >= control["rolling_1y"]["positive_rate"]
    g5 = candidate["full"]["mdd"] >= control["full"]["mdd"] - 0.02
    g6 = candidate["full"]["total_return"] >= control["full"]["total_return"] - 0.05
    g7 = (
        candidate["recent_12m"]["total_return"] is not None
        and candidate["recent_12m"]["total_return"] > 0
        and candidate["recent_12m"]["mdd"] >= control["recent_12m"]["mdd"] - 0.02
    )
    g8 = candidate["full"]["total_return"] > 0
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7, "G8": g8}
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R214 risk-adjusted momentum quality candidate versus a matched quality-cash control",
        "preregistration": (EVIDENCE / "r214_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "factor": {
            "id": "ext_alt_hs300_risk_adjusted_momentum_60d",
            "column": FACTOR_COLUMN,
            "formula": "60-return simple return divided by annualized 60-day realized volatility",
            "coverage": coverage,
        },
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "cost": "3x: 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 10,
            "position_sizing": "equal",
            "holding_days": 20,
            "stop_loss": -0.20,
            "universe": "historical HS300 membership; monthly 2018-2020, daily 2021-2026",
            "segments": [[str(start), str(end)] for start, end in SEGMENTS],
            "stitching": "segment returns compounded into one curve; first day of each later segment skipped",
        },
        "strategies": STRATEGIES,
        "runs": runs,
        "results": results,
        "decision": decision,
        "caveats": [
            "The full curve is stitched from three system-engine segments because a single 8-year matrix exceeded memory.",
            "Segment boundaries can reset an open position; each segment is also reported independently.",
            "The 2018-2020 membership snapshots are monthly; intra-month index changes are not modeled.",
            "The factor is research-only until every preregistered gate passes.",
            "Risk-adjusted momentum may overlap with raw momentum and low-volatility exposures; the matched control isolates its score contribution.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"coverage": coverage, "results": results, "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
