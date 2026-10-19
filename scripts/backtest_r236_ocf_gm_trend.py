#!/usr/bin/env python3
"""R236: system-engine test of the non-financial HS300 OCF margin + gross-margin trend composite."""
from __future__ import annotations

import gc
import importlib.util
import json
import statistics as st
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r236_ocf_gm_trend.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
CACHE = DATA / "research" / "r236_cache"
START = date(2022, 7, 1)
END = date(2026, 10, 16)
WINDOW = 252
SEGMENTS = [
    (date(2022, 7, 1), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_ocf_gm_trend_composite_h20_sl20",
    "control": "ai_hs300_ocf_gm_trend_composite_control",
}
FACTOR_COLUMN = "ocf_margin_4q"
LEVEL_FACTOR_COLUMN = "gross_margin_yoy_improvement_q"
R221_EVIDENCE = EVIDENCE / "r221_sga_nonfin_quality.json"
BANK = "\u94f6\u884c"
NON_BANK_FINANCE = "\u975e\u94f6\u91d1\u878d"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _membership_with_industry() -> pl.DataFrame:
    old = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    members = (
        pl.concat([old, new], how="diagonal_relaxed")
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    return members.join(industry, on="symbol", how="left")


def _nonfin_membership(members: pl.DataFrame) -> pl.DataFrame:
    return (
        members.filter(
            pl.col("industry_l1").is_not_null()
            & ~pl.col("industry_l1").is_in([BANK, NON_BANK_FINANCE])
        )
        .select(["effective_date", "symbol"])
        .sort(["effective_date", "symbol"])
    )


def _universe_audit(members: pl.DataFrame) -> dict:
    financial = pl.col("industry_l1").is_in([BANK, NON_BANK_FINANCE])
    excluded = members.filter(financial)
    missing = members.filter(pl.col("industry_l1").is_null())
    nonfin = _nonfin_membership(members)
    return {
        "industry_source": "current ext_hy_ths industry snapshot; not historical PIT",
        "excluded_industries": [BANK, NON_BANK_FINANCE],
        "member_snapshot_rows": members.height,
        "nonfinancial_member_rows": nonfin.height,
        "excluded_financial_member_rows": excluded.height,
        "industry_missing_member_rows": missing.height,
        "nonfinancial_symbols": nonfin["symbol"].n_unique(),
        "excluded_financial_symbols": excluded["symbol"].n_unique(),
        "industry_missing_symbols": missing["symbol"].n_unique(),
    }


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
        "discovery_2022_2024": r112._stats(_segment(curve, date(2022, 7, 1), date(2024, 12, 31))),
        "validation_2025_2026": r112._stats(_segment(curve, date(2025, 1, 2), END)),
        "rolling_1y": rolling,
        "recent_12m": {
            "total_return": round(_compound(rets[-WINDOW:]), 6) if len(rets) >= WINDOW else None,
            "mdd": round(_mdd(rets[-WINDOW:]), 6) if len(rets) >= WINDOW else None,
        },
    }


def _monthly_rank_correlation(frame: pl.DataFrame, left: str, right: str) -> tuple[pl.DataFrame, int]:
    both = frame.filter(pl.col(left).is_not_null() & pl.col(right).is_not_null())
    ranked = both.with_columns([
        pl.col(left).rank().over("date").alias("_rank_left"),
        pl.col(right).rank().over("date").alias("_rank_right"),
    ])
    daily = (
        ranked.group_by("date")
        .agg(pl.corr("_rank_left", "_rank_right").alias("corr"))
        .sort("date")
    )
    monthly = (
        daily.with_columns(pl.col("date").dt.truncate("1mo").alias("month"))
        .group_by("month")
        .agg(pl.col("corr").mean().alias("monthly_corr"))
        .sort("month")
    )
    return monthly, both.height


def _independence_stats(monthly: pl.DataFrame, overlap_rows: int) -> dict:
    return {
        "months": monthly.height,
        "overlap_member_rows": overlap_rows,
        "mean_abs_monthly_spearman": round(float(monthly["monthly_corr"].abs().mean()), 6),
        "mean_signed_monthly_spearman": round(float(monthly["monthly_corr"].mean()), 6),
        "min_monthly_spearman": round(float(monthly["monthly_corr"].min()), 6),
        "max_monthly_spearman": round(float(monthly["monthly_corr"].max()), 6),
    }


def _factor_coverage(membership: pl.DataFrame) -> dict:
    ext_root = DATA / "ext_data" / "alt_hs300" / "timeseries"
    factor_all = (
        pl.scan_parquet(str(ext_root / "date=*/part.parquet"), hive_partitioning=True)
        .filter(pl.col("date") <= END)
        .select(["symbol", "date", LEVEL_FACTOR_COLUMN, FACTOR_COLUMN])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date))
        .sort(["date", "symbol"])
    )
    first_valid = factor_all.filter(pl.col(FACTOR_COLUMN).is_not_null() & pl.col(LEVEL_FACTOR_COLUMN).is_not_null())["date"].min()
    factor = factor_all.filter(pl.col("date") >= START)
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
    corr_components, overlap_components = _monthly_rank_correlation(eligible, LEVEL_FACTOR_COLUMN, FACTOR_COLUMN)
    return {
        "eligible_member_rows": eligible.height,
        "valid_member_rows": int((eligible[FACTOR_COLUMN].is_not_null() & eligible[LEVEL_FACTOR_COLUMN].is_not_null()).sum()),
        "coverage_2022_2026": round(float((eligible[FACTOR_COLUMN].is_not_null() & eligible[LEVEL_FACTOR_COLUMN].is_not_null()).sum()) / eligible.height, 6),
        "recent_member_rows": recent.height,
        "recent_valid_rows": int((recent[FACTOR_COLUMN].is_not_null() & recent[LEVEL_FACTOR_COLUMN].is_not_null()).sum()),
        "recent_12m_coverage": round(float((recent[FACTOR_COLUMN].is_not_null() & recent[LEVEL_FACTOR_COLUMN].is_not_null()).sum()) / recent.height, 6),
        "first_valid_date": str(first_valid),
        "factor_min": round(float(factor[FACTOR_COLUMN].min()), 6),
        "factor_median": round(float(factor[FACTOR_COLUMN].median()), 6),
        "factor_max": round(float(factor[FACTOR_COLUMN].max()), 6),
        "independence": {
            "method": "daily cross-sectional Spearman rank correlation averaged within each calendar month, then mean absolute monthly correlation",
            "component_pair": "ocf_margin_4q vs gross_margin_yoy_improvement_q",
            "stats": _independence_stats(corr_components, overlap_components),
        },
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    all_members = _membership_with_industry()
    membership = _nonfin_membership(all_members)
    universe_audit = _universe_audit(all_members)
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

    d1 = coverage["coverage_2022_2026"] >= 0.90
    d2 = coverage["first_valid_date"] <= "2022-06-30" and coverage["recent_12m_coverage"] >= 0.95
    d3 = coverage["independence"]["stats"]["mean_abs_monthly_spearman"] <= 0.70
    g1 = candidate["discovery_2022_2024"]["total_return"] >= control["discovery_2022_2024"]["total_return"] + 0.05
    g2 = candidate["discovery_2022_2024"]["mdd"] >= control["discovery_2022_2024"]["mdd"] - 0.02
    g3 = candidate["validation_2025_2026"]["total_return"] >= control["validation_2025_2026"]["total_return"] - 0.05
    g4 = candidate["rolling_1y"]["positive_rate"] >= control["rolling_1y"]["positive_rate"]
    g5 = candidate["full"]["mdd"] >= control["full"]["mdd"] - 0.02
    g6 = candidate["full"]["total_return"] >= control["full"]["total_return"] - 0.05
    g7 = (
        candidate["recent_12m"]["total_return"] is not None
        and candidate["recent_12m"]["total_return"] > 0
        and candidate["recent_12m"]["mdd"] >= control["recent_12m"]["mdd"] - 0.02
    )
    g8 = candidate["full"]["total_return"] > 0
    with R221_EVIDENCE.open(encoding="utf-8") as handle:
        level_evidence = json.load(handle)
    level_benchmark_return = level_evidence["results"]["candidate"]["full"]["total_return"]
    g9 = candidate["full"]["total_return"] >= level_benchmark_return - 0.05
    decision = {
        "D1": d1, "D2": d2, "D3": d3,
        "G1": g1, "G2": g2, "G3": g3, "G4": g4,
        "G5": g5, "G6": g6, "G7": g7, "G8": g8, "G9": g9,
    }
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R236 non-financial HS300 OCF margin plus gross-margin trend equal-weight composite versus a matched quality-cash control",
        "preregistration": (EVIDENCE / "r236_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "universe": universe_audit,
        "factor": {
            "id": "research_composite_ocf_gm_trend",
            "components": {"ext_alt_hs300_ocf_margin_4q": 0.5, "ext_alt_hs300_gross_margin_yoy_improvement_q": 0.5},
            "formula": "50% ext_alt_hs300_ocf_margin_4q + 50% ext_alt_hs300_gross_margin_yoy_improvement_q; both components must be non-null",
            "effective_date": "each component keeps its own effective date; both use the maximum statutory disclosure deadline of four quarters plus four calendar days",
            "first_valid_date_scope": "all factor history through END before applying the START coverage window",
            "includes": "TTM operating cash flow over revenue and single-quarter gross-margin YoY improvement",
            "coverage": coverage,
        },
        "samples": {
            "discovery": ["2022-07-01", "2024-12-31"],
            "validation": ["2025-01-02", str(END)],
            "rolling_1y": "252-trading-day rolling compounded return over the stitched 2022-2026 curve",
            "recent_12m": "last 252 trading-day returns of the stitched 2022-2026 curve",
        },
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "cost": "3x: 6bp commission + 15bp slippage + 10bp stamp tax",
            "max_positions": 10,
            "position_sizing": "equal",
            "holding_days": 20,
            "stop_loss": -0.20,
            "universe": "historical HS300 membership excluding banks and non-bank financials; current industry snapshot applied to history",
            "segments": [[str(start), str(end)] for start, end in SEGMENTS],
            "stitching": "two segment returns compounded into one curve; first day of each later segment skipped",
        },
        "related_benchmark": {
            "strategy_id": "ai_hs300_sga_nonfin_quality_h20_sl20",
            "source_evidence": R221_EVIDENCE.relative_to(ROOT).as_posix(),
            "full_total_return": level_benchmark_return,
            "g9_threshold": round(level_benchmark_return - 0.05, 6),
        },
        "strategies": STRATEGIES,
        "runs": runs,
        "results": results,
        "decision": decision,
        "caveats": [
            "The clean point-in-time sample starts in 2022; 2018-2020 financial data are excluded because reliable first-disclosure dates are unavailable.",
            "The factor uses statutory disclosure deadlines as a fallback because provider announcement dates are not reliable first-disclosure timestamps.",
            "Local financial tables are current snapshots, so restatement risk remains and is not fully eliminated.",
            "Equal component weights are a fixed prior, not an optimized allocation.",
            "The full curve is stitched from two system-engine segments because the combined matrix exceeded memory; segment boundaries can reset an open position.",
            "The factor is research-only until every preregistered gate passes.",
            "Candidate and control share the same non-financial HS300 universe, two-component availability filter, and execution; the control excludes both components from scoring.",
            "Banks and non-bank financials are excluded because operating cash flow and gross-margin definitions are not economically comparable for those firms.",
            "Industry classification is a current snapshot applied to history, not a point-in-time industry series.",
            "D3 checks the average monthly cross-sectional rank correlation between the two components; the audited value before preregistration was 0.118635 over 52 months.",
            "Both components must be non-null; no filling and no fallback to either single factor is used.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"coverage": coverage, "results": results, "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
