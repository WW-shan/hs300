#!/usr/bin/env python3
"""R163: extend the dual-sleeve candidate back to 2018-01-02.

Both sleeves are rerun end to end on the extended historical membership and
the rebuilt ext_data holder fields. Execution remains T+1 open, 10 positions,
20-day holding, fixed -20% stop and 3x cost.
"""
from __future__ import annotations

import importlib.util
import json
import random
import statistics as st
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r163_extended_a_sleeve.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
CACHE = DATA / "research" / "r163_cache"
MEMBERSHIP = DATA / "research" / "r157_membership" / "monthly_snapshots.parquet"
BATCH_MANIFEST = DATA / "research" / "r163_holder_batch" / "manifest.json"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2018, 1, 2)
END = date(2026, 10, 16)
WINDOW = 252
BLOCK = 20
PATHS = 2000
SEED = 20261020
QUARTERS = ["20180331", "20180630", "20180930", "20181231", "20190331", "20190630", "20190930", "20191231"]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _membership() -> pl.DataFrame:
    old = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(MEMBERSHIP).select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    return (
        pl.concat([new, old])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def _patched_membership(membership: pl.DataFrame):
    from app.backtest import strategy as strategy_module
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def _expected_symbols(membership: pl.DataFrame, quarter: str) -> set[str]:
    quarter_end = datetime.strptime(quarter, "%Y%m%d").date()
    eligible = membership.filter(pl.col("effective_date") <= quarter_end)
    if eligible.is_empty():
        return set()
    latest = eligible["effective_date"].max()
    return set(eligible.filter(pl.col("effective_date") == latest)["symbol"].to_list())


def _run(service, membership: pl.DataFrame, sid: str) -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{sid}_{START.isoformat()}_{END.isoformat()}.parquet"
    if path.exists():
        frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}
    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=START, end=END,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    with _patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{sid}: {result.error}")
    frame = r112._curve_frame(result.equity_curve)
    frame.write_parquet(path)
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
    }


def _segment(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))]


def _bootstrap(values: list[float]) -> dict:
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    rng = random.Random(SEED)
    n_blocks = (WINDOW + BLOCK - 1) // BLOCK
    max_start = len(returns) - BLOCK
    paths = []
    for _ in range(PATHS):
        path = []
        for _ in range(n_blocks):
            start = rng.randint(0, max_start)
            path.extend(returns[start : start + BLOCK])
        value = 1.0
        for ret in path[:WINDOW]:
            value *= 1.0 + ret
        paths.append(value - 1.0)
    return {
        "positive_rate": round(sum(v > 0 for v in paths) / len(paths), 4),
        "p05": round(_quantile(paths, 0.05), 4),
        "median": round(st.median(paths), 4),
        "worst": round(min(paths), 4),
        "best": round(max(paths), 4),
    }


def _yearly(curve: pl.DataFrame) -> dict:
    out = {}
    for year in range(START.year, END.year + 1):
        part = _segment(curve, date(year, 1, 1), date(year, 12, 31))
        if part.height >= 2:
            out[str(year)] = r112._stats(part)
    return out


def _index_stats() -> dict | None:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        return None
    idx = (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    if idx.height < 2:
        return None
    return r112._stats(idx.rename({"close": "value"}))


def main() -> int:
    global r112
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15

    membership = _membership()
    service = r112._service()
    curves = {}
    runs = {}
    for sid in (A_SLEEVE, B_SLEEVE):
        curves[sid], runs[sid] = _run(service, membership, sid)
        print(f"ran {sid}: {runs[sid]}", flush=True)

    a, b = curves[A_SLEEVE], curves[B_SLEEVE]
    risk, weights = r112._combine(a, b, "risk")
    candidate = r114._vol_target(risk)["curve"].sort("date")
    values = candidate["value"].to_list()
    rolling = r127._summarize(r127._window_stats(values))
    bootstrap = _bootstrap(values)

    batch = json.loads(BATCH_MANIFEST.read_text(encoding="utf-8"))
    quarter_coverage = {}
    total_expected = total_covered = 0
    for quarter in QUARTERS:
        expected = _expected_symbols(membership, quarter)
        item = batch["quarters"].get(quarter, {})
        covered = int(item.get("covered_expected", 0))
        quarter_coverage[quarter] = {
            "expected": len(expected),
            "covered": covered,
            "coverage": round(covered / len(expected), 6) if expected else None,
        }
        total_expected += len(expected)
        total_covered += covered
    overall_coverage = total_covered / total_expected if total_expected else None

    spot_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / "date=2018-04-23" / "part.parquet"
    spot = None
    if spot_path.exists():
        row = pl.read_parquet(spot_path).filter(pl.col("symbol") == "000001.SZ")
        if row.height:
            spot = {
                "date": "2018-04-23",
                "symbol": "000001.SZ",
                "holder_top10_free_float_share": row["holder_top10_free_float_share"][0],
            }

    a_2018_2019 = r112._stats(_segment(a, date(2018, 1, 1), date(2019, 12, 31)))
    a_2018 = r112._stats(_segment(a, date(2018, 1, 1), date(2018, 12, 31)))
    b_2018_2020 = r112._stats(_segment(b, date(2018, 1, 1), date(2020, 12, 31)))
    candidate_2018 = r112._stats(_segment(candidate, date(2018, 1, 1), date(2018, 12, 31)))
    candidate_2018_2020 = r112._stats(_segment(candidate, date(2018, 1, 1), date(2020, 12, 31)))
    candidate_full = r112._stats(candidate)
    index_full = _index_stats()

    d1 = bool(batch.get("all_quarters_fetched") and batch.get("min_coverage", 0) >= 0.95)
    d2 = bool(overall_coverage is not None and overall_coverage >= 0.70 and spot is not None)
    g1 = a_2018_2019["total_return"] > 0
    g2 = rolling["positive_rate"] >= 0.70
    g3 = bootstrap["positive_rate"] >= 0.65
    g4 = candidate_2018["mdd"] >= -0.20
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R163 extend the dual-sleeve candidate to 2018",
        "window": [str(START), str(END)],
        "execution": {
            "matching": "open_t+1",
            "cost": "3x: 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 10,
            "holding_days": 20,
            "stop_loss": -0.20,
            "portfolio_risk_parity": "60-day inverse volatility, [0.2,0.8], monthly, 0.52% round trip",
            "vol_target": "15%, 60-day, [0.2,1.0], monthly, 0.52% round trip",
        },
        "runs": runs,
        "data_checks": {
            "batch_manifest": batch,
            "quarter_coverage": quarter_coverage,
            "overall_coverage": round(overall_coverage, 6) if overall_coverage is not None else None,
            "ext_data_spot_check": spot,
        },
        "sleeves": {
            "A_2018_2019": a_2018_2019,
            "A_2018": a_2018,
            "B_2018_2020": b_2018_2020,
        },
        "candidate": {
            "full": candidate_full,
            "2018": candidate_2018,
            "2018_2020": candidate_2018_2020,
            "yearly": _yearly(candidate),
            "rolling_1y": rolling,
            "bootstrap": bootstrap,
            "index_full": index_full,
            "risk_parity_weight_a_mean": round(st.mean(w["weight_a"] for w in weights), 4),
        },
        "decision": decision,
        "caveats": [
            "Membership is monthly before 2020-12-31 and daily afterwards.",
            "Holder effective date uses max(NOTICE_DATE, UPDATE_DATE)+1 day to avoid restatement leakage.",
            "The 2018-2020 membership snapshots are monthly, so intra-month index changes are not modeled.",
            "The candidate remains a research result, not a production recommendation.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("A 2018-2019:", json.dumps(a_2018_2019, ensure_ascii=False), flush=True)
    print("candidate full:", json.dumps(candidate_full, ensure_ascii=False), flush=True)
    print("candidate 2018:", json.dumps(candidate_2018, ensure_ascii=False), flush=True)
    print("rolling:", json.dumps(rolling, ensure_ascii=False), flush=True)
    print("bootstrap:", json.dumps(bootstrap, ensure_ascii=False), flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
