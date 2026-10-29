#!/usr/bin/env python3
"""R272 feasibility-only audit; intentionally does not read forward returns."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r272_feasibility_preregistration.json"
START, END = date(2023, 5, 4), date(2026, 10, 16)
MAX_AGE_DAYS = 180
FACTOR = "operating_margin_yoy_change_q"
COMPARISONS = {
    "ocf_margin_4q": ("alt_hs300", "ocf_margin_4q"),
    "cash_to_assets": ("alt_hs300", "cash_to_assets"),
    "sga_ratio_4q_low": ("alt_hs300", "sga_ratio_4q_low"),
    "gross_margin_yoy_improvement_q": ("alt_hs300", "gross_margin_yoy_improvement_q"),
    "gross_margin_stability_4q_low": ("alt_hs300", "gross_margin_stability_4q_low"),
    "risk_adjusted_momentum_60d": ("alt_hs300", "risk_adjusted_momentum_60d"),
    "trend_efficiency_60d": ("alt_hs300", "trend_efficiency_60d"),
    "idio_vol_60d_low": ("alt_hs300", "idio_vol_60d_low"),
    "stock_rel_industry_20d": ("alt_hs300", "stock_rel_industry_20d"),
    "capex_intensity_low": ("alt_hs300", "capex_intensity_low"),
    "free_cash_flow_assets": ("alt_hs300_fcf", "free_cash_flow_assets"),
    "asset_growth_yoy_low": ("alt_hs300_ag", "asset_growth_yoy_low"),
    "working_capital_change_yoy_low": ("alt_hs300_wc", "working_capital_change_yoy_low"),
    "gross_profitability": ("alt_hs300_gp", "gross_profitability"),
    "piotroski7_score": ("alt_hs300_pf7", "piotroski7_score"),
    "management_ocf_gm_min_rank": ("alt_hs300_minrank", "management_ocf_gm_min_rank"),
    "quality_relative_strength_min_rank": ("alt_hs300_qrs", "quality_relative_strength_min_rank"),
}
FINANCIAL_INPUT = DATA / "financials/income/part.parquet"
MEMBERSHIP_INPUT = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
INDUSTRY_INPUT = DATA / "ext_data/ext_hy_ths/part.parquet"
BASE_ROOT = DATA / "ext_data/alt_hs300/timeseries"
EXCLUDED = ("银行", "非银金融")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deadline() -> pl.Expr:
    year = pl.col("period_end").dt.year()
    month = pl.col("period_end").dt.month()
    return (
        pl.when(month == 3).then(pl.date(year, 4, 30))
        .when(month == 6).then(pl.date(year, 8, 31))
        .when(month == 9).then(pl.date(year, 10, 31))
        .when(month == 12).then(pl.date(year + 1, 4, 30))
        .otherwise(pl.col("period_end") + pl.duration(days=120))
    )


def _financial_factor() -> pl.DataFrame:
    required = {"symbol", "period_end", "announce_date", "revenue", "operating_profit"}
    schema = pl.scan_parquet(FINANCIAL_INPUT).collect_schema().names()
    if not required.issubset(schema):
        raise RuntimeError(f"income table missing {sorted(required - set(schema))}")
    raw = (
        pl.read_parquet(FINANCIAL_INPUT, columns=sorted(required))
        .with_columns([
            pl.col("symbol").cast(pl.String),
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("_raw_announce"),
            pl.col("revenue").cast(pl.Float64),
            pl.col("operating_profit").cast(pl.Float64),
        ])
        .sort(["symbol", "period_end", "_raw_announce"])
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            _deadline().alias("_deadline"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_raw_announce") > pl.col("period_end"))
                & (pl.col("_raw_announce") <= pl.col("_deadline"))
            )
            .then(pl.col("_raw_announce"))
            .otherwise(pl.col("_deadline") + pl.duration(days=3))
            .alias("_pit")
        )
        .with_columns([
            pl.col("_period_index").shift(1).over("symbol").alias("_prev_index"),
            pl.col("revenue").shift(1).over("symbol").alias("_prev_revenue"),
            pl.col("operating_profit").shift(1).over("symbol").alias("_prev_operating_profit"),
            pl.col("_pit").shift(1).over("symbol").alias("_prev_pit"),
        ])
        .with_columns((pl.col("_period_index") - pl.col("_prev_index")).alias("_period_gap"))
        .with_columns([
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("revenue"))
            .when((pl.col("_period_gap") == 1) & (pl.col("_prev_index").is_not_null()))
            .then(pl.col("revenue") - pl.col("_prev_revenue"))
            .otherwise(None).alias("_q_revenue"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("operating_profit"))
            .when((pl.col("_period_gap") == 1) & (pl.col("_prev_index").is_not_null()))
            .then(pl.col("operating_profit") - pl.col("_prev_operating_profit"))
            .otherwise(None).alias("_q_operating_profit"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
            .then(pl.col("_pit"))
            .when((pl.col("_period_gap") == 1) & pl.col("_prev_pit").is_not_null())
            .then(pl.max_horizontal("_pit", "_prev_pit"))
            .otherwise(None).alias("_q_available"),
        ])
        .with_columns(
            pl.when(pl.col("_q_revenue") > 0)
            .then(pl.col("_q_operating_profit") / pl.col("_q_revenue"))
            .otherwise(None).alias("_q_margin")
        )
        .with_columns([
            pl.col("_period_index").shift(4).over("symbol").alias("_yoy_index"),
            pl.col("_q_margin").shift(4).over("symbol").alias("_yoy_margin"),
            pl.col("_q_available").shift(4).over("symbol").alias("_yoy_available"),
        ])
        .with_columns([
            (pl.col("_q_margin") - pl.col("_yoy_margin")).alias(FACTOR),
            pl.when((pl.col("_period_index") - pl.col("_yoy_index") == 4)
                    & pl.col("_q_available").is_not_null()
                    & pl.col("_yoy_available").is_not_null())
            .then(pl.max_horizontal("_q_available", "_yoy_available"))
            .otherwise(None).alias("_factor_available"),
        ])
        .filter(pl.col(FACTOR).is_finite() & pl.col("_factor_available").is_not_null())
        .with_columns((pl.col("_factor_available") + pl.duration(days=1)).alias("available_date"))
        .select(["symbol", "available_date", FACTOR])
        .sort(["symbol", "available_date"])
    )
    return raw


def _calendar() -> pl.DataFrame:
    paths = sorted(BASE_ROOT.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"base factor calendar missing: {BASE_ROOT}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select(pl.col("date").cast(pl.Date))
        .unique().sort("date").collect()
        .filter(pl.col("date").is_between(START, END, closed="both"))
    )


def _membership(calendar: pl.DataFrame) -> pl.DataFrame:
    snapshots = (
        pl.read_parquet(MEMBERSHIP_INPUT, columns=["query_date", "symbol"])
        .with_columns([pl.col("query_date").cast(pl.Date), pl.col("symbol").cast(pl.String)])
        .unique(subset=["query_date", "symbol"])
        .sort(["query_date", "symbol"])
    )
    snapshot_dates = snapshots.select("query_date").unique().sort("query_date")
    day_map = calendar.sort("date").join_asof(
        snapshot_dates, left_on="date", right_on="query_date", strategy="backward"
    )
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"])
    return (
        day_map.join(snapshots, on="query_date", how="inner")
        .join(industry, on="symbol", how="left")
        .filter(pl.col("industry_l1").is_not_null() & ~pl.col("industry_l1").is_in(EXCLUDED))
        .select(["date", "symbol"]).unique().sort(["symbol", "date"])
    )


def _attach_candidate(members: pl.DataFrame, records: pl.DataFrame) -> pl.DataFrame:
    return (
        members.sort(["symbol", "date"])
        .join_asof(records.sort(["symbol", "available_date"]),
                   left_on="date", right_on="available_date", by="symbol",
                   strategy="backward", check_sortedness=False)
        .filter((pl.col("date") - pl.col("available_date")).dt.total_days() <= MAX_AGE_DAYS)
        .select(["date", "symbol", FACTOR])
    )


def _read_factor(config: str, field: str, start: date, end: date) -> pl.DataFrame:
    root = DATA / "ext_data" / config / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError(f"comparison factor missing: {config}.{field}")
    return (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select([pl.col("symbol").cast(pl.String), pl.col("date").cast(pl.Date), pl.col(field).cast(pl.Float64)])
        .filter(pl.col("date").is_between(start, end, closed="both"))
        .collect().unique(subset=["symbol", "date"], keep="last")
    )


def _rank_correlation(left: pl.DataFrame, right: pl.DataFrame, field: str) -> float | None:
    joined = left.join(right, on=["date", "symbol"], how="inner").drop_nulls([FACTOR, field])
    if joined.height < 30 or joined[FACTOR].n_unique() < 2 or joined[field].n_unique() < 2:
        return None
    value = joined.select(
        pl.corr(pl.col(FACTOR).rank(method="average"), pl.col(field).rank(method="average"))
    ).item()
    return None if value is None else float(value)


def _correlations(candidate: pl.DataFrame, calendar: pl.DataFrame) -> dict:
    month_ends = (
        calendar.with_columns([pl.col("date").dt.year().alias("_year"), pl.col("date").dt.month().alias("_month")])
        .group_by(["_year", "_month"]).agg(pl.col("date").max()).sort("date")["date"].to_list()
    )
    candidate_month = candidate.filter(pl.col("date").is_in(month_ends))
    result = {}
    for name, (config, field) in COMPARISONS.items():
        comparison = _read_factor(config, field, START, END).filter(pl.col("date").is_in(month_ends))
        monthly = []
        for day in month_ends:
            left = candidate_month.filter(pl.col("date") == day)
            right = comparison.filter(pl.col("date") == day)
            corr = _rank_correlation(left, right, field)
            if corr is not None:
                monthly.append({"date": str(day), "spearman": round(corr, 6), "names": left.join(right, on=["date", "symbol"], how="inner").drop_nulls([FACTOR, field]).height})
        abs_values = [abs(row["spearman"]) for row in monthly]
        result[name] = {
            "months": len(monthly),
            "mean_abs_spearman": round(sum(abs_values) / len(abs_values), 6) if abs_values else None,
            "mean_signed_spearman": round(sum(row["spearman"] for row in monthly) / len(monthly), 6) if monthly else None,
            "monthly": monthly,
        }
    observed = [row["mean_abs_spearman"] for row in result.values() if row["mean_abs_spearman"] is not None]
    return {"factors": result, "max_mean_abs_spearman": max(observed) if observed else None, "month_end_dates": [str(day) for day in month_ends]}


def main() -> int:
    if not PREREG.exists():
        raise RuntimeError("run scripts/preregister_r272_feasibility.py first")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("probe_sha256") != _sha256(Path(__file__).resolve()):
        raise RuntimeError("probe changed after feasibility preregistration")
    forbidden_fragments = ("forward_" + "return", "trades" + ".json", "kline_daily_" + "enriched")
    if any(token in Path(__file__).read_text(encoding="utf-8") for token in forbidden_fragments):
        raise RuntimeError("probe source contains a prohibited return/backtest input reference")

    calendar = _calendar()
    members = _membership(calendar)
    records = _financial_factor()
    candidate = _attach_candidate(members, records)
    recent_dates = calendar["date"].tail(252).to_list()
    full_valid = candidate.height
    full_rows = members.height
    recent_members = members.filter(pl.col("date").is_in(recent_dates))
    recent_candidate = candidate.filter(pl.col("date").is_in(recent_dates))
    first_valid = candidate["date"].min() if candidate.height else None
    daily = (
        members.group_by("date").agg(pl.len().alias("eligible"))
        .join(candidate.group_by("date").agg([pl.len().alias("valid"), pl.col(FACTOR).n_unique().alias("unique_values")]), on="date", how="left")
        .with_columns(pl.col("valid").fill_null(0), pl.col("unique_values").fill_null(0))
        .sort("date")
    )
    nonconstant_days = int(daily.filter((pl.col("eligible") >= 30) & (pl.col("unique_values") > 1)).height)
    eligible_days = int(daily.filter(pl.col("eligible") >= 30).height)
    distribution = candidate.select([
        pl.col(FACTOR).min().alias("min"),
        pl.col(FACTOR).quantile(0.01).alias("p01"),
        pl.col(FACTOR).median().alias("median"),
        pl.col(FACTOR).quantile(0.99).alias("p99"),
        pl.col(FACTOR).max().alias("max"),
        (pl.col(FACTOR) == 0).mean().alias("zero_rate"),
    ]).row(0, named=True) if candidate.height else {}
    correlations = _correlations(candidate, calendar)
    coverage = {
        "eligible_member_rows": full_rows,
        "valid_member_rows": full_valid,
        "full_coverage": round(full_valid / full_rows, 6) if full_rows else 0.0,
        "recent_member_rows": recent_members.height,
        "recent_valid_member_rows": recent_candidate.height,
        "recent_252_trading_day_coverage": round(recent_candidate.height / recent_members.height, 6) if recent_members.height else 0.0,
        "first_valid_date": str(first_valid) if first_valid else None,
        "last_valid_date": str(candidate["date"].max()) if candidate.height else None,
        "eligible_dates": calendar.height,
        "nonconstant_dates_with_30_names": nonconstant_days,
        "dates_with_30_or_more_names": eligible_days,
        "nonconstant_day_rate": round(nonconstant_days / eligible_days, 6) if eligible_days else 0.0,
        "by_year": [],
    }
    for year in range(START.year, END.year + 1):
        year_members = members.filter(pl.col("date").dt.year() == year)
        year_candidate = candidate.filter(pl.col("date").dt.year() == year)
        coverage["by_year"].append({
            "year": year, "member_rows": year_members.height, "valid_rows": year_candidate.height,
            "coverage": round(year_candidate.height / year_members.height, 6) if year_members.height else 0.0,
        })
    max_corr = correlations["max_mean_abs_spearman"]
    gates = {
        "F1": coverage["full_coverage"] >= 0.99,
        "F2": coverage["recent_252_trading_day_coverage"] >= 0.99,
        "F3": first_valid is not None and first_valid <= START,
        "F4": len(correlations["month_end_dates"]) >= 24 and max_corr is not None and max_corr <= 0.70
                and all(item["months"] >= 24 for item in correlations["factors"].values()),
        "F5": eligible_days > 0 and nonconstant_days / eligible_days >= 0.95,
    }
    payload = {
        "round": "R272-feasibility",
        "status": "feasibility_only_no_outcome_analysis",
        "factor": FACTOR,
        "formula": "YoY change in the single-quarter operating_profit / revenue ratio; quarterly cumulative flows are differenced only across consecutive same-fiscal-year quarters.",
        "pit_rule": prereg["factor_definition"]["point_in_time"],
        "window": [str(START), str(END)],
        "age_limit_calendar_days": MAX_AGE_DAYS,
        "universe": {"source": MEMBERSHIP_INPUT.relative_to(ROOT).as_posix(), "industry_source": INDUSTRY_INPUT.relative_to(ROOT).as_posix(), "excluded_current_industries": list(EXCLUDED), "point_in_time_industry": False},
        "inputs": {
            "income_table": {"path": FINANCIAL_INPUT.relative_to(ROOT).as_posix(), "sha256": _sha256(FINANCIAL_INPUT)},
            "membership": {"path": MEMBERSHIP_INPUT.relative_to(ROOT).as_posix(), "sha256": _sha256(MEMBERSHIP_INPUT)},
            "industry": {"path": INDUSTRY_INPUT.relative_to(ROOT).as_posix(), "sha256": _sha256(INDUSTRY_INPUT)},
        },
        "coverage": coverage,
        "distribution": {key: round(float(value), 8) if value is not None else None for key, value in distribution.items()},
        "independence": correlations,
        "gates": gates,
        "all_feasibility_gates_pass": all(gates.values()),
        "outcome_data_loaded": False,
        "data_scope": "financial input, historical membership, current industry mapping, prebuilt factor values, and trading-date calendar only",
        "preregistration": PREREG.relative_to(ROOT).as_posix(),
        "preregistration_sha256": _sha256(PREREG),
        "probe_sha256": _sha256(Path(__file__).resolve()),
        "limitations": prereg["known_limitations"],
    }
    out = EVIDENCE / "r272_data_feasibility.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(out)
    out.with_suffix(".json.sha256").write_text(digest + "  " + out.name + "\n", encoding="ascii")
    print(json.dumps({
        "result": out.relative_to(ROOT).as_posix(),
        "sha256": digest,
        "coverage": coverage,
        "distribution": payload["distribution"],
        "max_mean_abs_spearman": max_corr,
        "gates": gates,
        "all_feasibility_gates_pass": payload["all_feasibility_gates_pass"],
        "outcome_data_loaded": False,
    }, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload["all_feasibility_gates_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
