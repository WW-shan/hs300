#!/usr/bin/env python3
"""Create a descriptive, post-hoc R274 comparison with the HS300 index."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
R274_RESULT = EVIDENCE / "r274_no_macro_gate.json"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
OUT = EVIDENCE / "r274_vs_hs300_audit.json"
START, END = date(2021, 6, 1), date(2026, 10, 16)
VALIDATION_START = date(2024, 1, 1)
DISCOVERY_END = date(2023, 12, 31)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stats(values: list[float]) -> dict:
    if len(values) < 2:
        raise RuntimeError("at least two aligned closes are required")
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values))]
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    total = values[-1] / values[0] - 1
    deviation = statistics.pstdev(returns)
    return {
        "total_return": round(total, 6),
        "annual_return": round((1 + total) ** (252 / len(returns)) - 1, 6),
        "sharpe_zero_rf": round(statistics.mean(returns) / deviation * math.sqrt(252), 4) if deviation else 0.0,
        "annualized_volatility": round(deviation * math.sqrt(252), 6),
        "max_drawdown": round(drawdown, 6),
        "observations": len(values),
    }


def _rebased(frame: pl.DataFrame, column: str, start: date, end: date) -> list[float]:
    prior = frame.filter(pl.col("date") < start).sort("date").tail(1)
    selected = frame.filter(pl.col("date").is_between(start, end, closed="both")).sort("date")
    if prior.height:
        selected = pl.concat([prior, selected]).unique(subset=["date"], keep="last").sort("date")
    values = selected[column].to_list()
    if len(values) < 2 or values[0] <= 0:
        raise RuntimeError(f"insufficient {column} data for {start}..{end}")
    base = values[0]
    return [value / base * 1_000_000 for value in values]


def _main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R274 benchmark audit exists; refusing overwrite")
    result = json.loads(R274_RESULT.read_text(encoding="utf-8"))
    control = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    run = result["runs"]["3x"]
    curve_path = DATA / "research/r274_cache/ai_hs300_macro_gate_quality_r274_no_macro_gate_3x_2021-06-01_2026-10-16.parquet"
    control_path = ROOT / control["runs"]["3x"]["curve_cache"]["path"]
    if _sha256(curve_path) != run["curve_sha256"]:
        raise RuntimeError("R274 curve does not match finalized result evidence")
    if _sha256(control_path) != control["runs"]["3x"]["curve_cache"]["sha256"]:
        raise RuntimeError("R266 control curve does not match finalized evidence")
    index_paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not index_paths:
        raise RuntimeError("index daily data is unavailable")
    index = (
        pl.scan_parquet([str(path) for path in index_paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select("date", "close")
        .collect()
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .sort("date")
    )
    candidate = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    baseline = pl.read_parquet(control_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    joined = (
        candidate.rename({"value": "candidate"})
        .join(baseline.rename({"value": "r266"}), on="date", how="inner")
        .join(index, on="date", how="inner")
        .sort("date")
    )
    if joined.height != 1303:
        raise RuntimeError(f"expected 1303 aligned trading dates, got {joined.height}")
    periods = {
        "full": (START, END),
        "discovery": (START, DISCOVERY_END),
        "validation": (VALIDATION_START, END),
    }
    report = {}
    for label, (period_start, period_end) in periods.items():
        report[label] = {
            name: _stats(_rebased(joined, column, period_start, period_end))
            for name, column in (("r274", "candidate"), ("r266", "r266"), ("hs300_price_only", "close"))
        }
    full = joined.filter(pl.col("date").is_between(START, END, closed="both"))
    candidate_returns = full["candidate"].pct_change().drop_nulls().to_list()
    index_returns = full["close"].pct_change().drop_nulls().to_list()
    mean_market = statistics.mean(index_returns)
    mean_candidate = statistics.mean(candidate_returns)
    covariance = statistics.mean((x - mean_market) * (y - mean_candidate) for x, y in zip(index_returns, candidate_returns, strict=True))
    variance = statistics.pvariance(index_returns)
    beta = covariance / variance if variance else 0.0
    report["full"]["paired_daily_diagnostics"] = {
        "beta_to_price_index": round(beta, 4),
        "annualized_intercept_zero_rf": round((mean_candidate - beta * mean_market) * 252, 6),
        "active_return_information_ratio": round(
            statistics.mean(x - y for x, y in zip(candidate_returns, index_returns, strict=True))
            / statistics.pstdev(x - y for x, y in zip(candidate_returns, index_returns, strict=True))
            * math.sqrt(252),
            4,
        ),
    }
    report["annual_calendar_years"] = {}
    for year in range(2021, 2027):
        year_start = max(START, date(year, 1, 1))
        year_end = min(END, date(year, 12, 31))
        if year_start > year_end:
            continue
        report["annual_calendar_years"][str(year)] = {
            name: _stats(_rebased(joined, column, year_start, year_end))
            for name, column in (("r274", "candidate"), ("r266", "r266"), ("hs300_price_only", "close"))
        }
    inputs = {
        "r274_result_sha256": _sha256(R274_RESULT),
        "r274_curve_sha256": _sha256(curve_path),
        "r266_result_sha256": _sha256(R266_RESULT),
        "r266_curve_sha256": _sha256(control_path),
        "hs300_index_partitions": {path.relative_to(ROOT).as_posix(): _sha256(path) for path in index_paths},
    }
    payload = {
        "round": "R274",
        "classification": "post_hoc_descriptive_benchmark_audit_not_preregistered_and_not_a_gate",
        "window": [str(START), str(END)],
        "index_symbol": "000300.SH",
        "index_return_type": "price_only_excludes_dividends",
        "method": "daily curves aligned by date; each requested period rebased to its first available observation, including prior close for subperiods",
        "results": report,
        "inputs": inputs,
        "interpretation": "Descriptive relative performance only. The historical window and its validation segment have been repeatedly examined; this is not independent alpha confirmation.",
        "production_status": "pause_production",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"audit": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "full": report["full"], "discovery": report["discovery"], "validation": report["validation"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
