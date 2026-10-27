#!/usr/bin/env python3
"""R254: system-engine test of the non-financial HS300 industry-relative weakest-link factor."""
from __future__ import annotations

import bisect
import gc
import importlib.util
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r254_industry_weakest_link.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R166_SCRIPT = ROOT / "scripts" / "backtest_r166_weak_regime_factor_scan.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R254_BUILDER = ROOT / "scripts" / "build_r254_industry_min_rank_factor.py"
CACHE = DATA / "research" / "r254_cache"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
WINDOW = 252
SEGMENTS = [
    (date(2023, 5, 4), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_industry_management_ocf_gm_minrank_quality_h20_sl20",
    "control": "ai_hs300_industry_management_ocf_gm_minrank_quality_control",
}
FACTOR_ID = "ext_alt_hs300_indminrank_industry_management_ocf_gm_min_rank"
FACTOR_COLUMN = "industry_management_ocf_gm_min_rank"
BASE_EXT_FIELDS = (
    "ocf_margin_4q",
    "cash_to_assets",
    "sga_ratio_4q_low",
    "gross_margin_yoy_improvement_q",
    "gross_margin_stability_4q_low",
    "risk_adjusted_momentum_60d",
    "trend_efficiency_60d",
    "idio_vol_60d_low",
    "stock_rel_industry_20d",
    "capex_intensity_low",
)
CORRELATION_FIELDS = (
    *BASE_EXT_FIELDS,
    "free_cash_flow_assets",
    "asset_growth_yoy_low",
    "working_capital_change_yoy_low",
    "gross_profitability",
    "piotroski7_score",
)

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _benchmark_dates(r229, start: date, end: date) -> list[date]:
    return r229._benchmark_dates(start, end)


def _run_segment(service, membership, r112, r229, sid: str, start: date, end: date, cost: float) -> tuple[pl.DataFrame, dict, list[dict]]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{sid}_{cost:g}x_{start.isoformat()}_{end.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and curve_path.stat().st_size and trades_path.exists() and trades_path.stat().st_size:
        frame = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}, trades

    scale = cost / 3.0
    cfg = r112.StrategyBacktestConfig(
        strategy_id=sid, symbols=None, start=start, end=end,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006 * scale,
        commission_pct=0.0006 * scale,
        stamp_tax_pct=0.001 * scale,
        slippage_bps=15.0 * scale,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "\u672a\u4ea7\u751f\u4e70\u5165\u4fe1\u53f7" in str(result.error):
            frame = pl.DataFrame({"date": _benchmark_dates(r229, start, end)}).with_columns(
                pl.lit(1_000_000.0).alias("value")
            )
            trades = []
        else:
            raise RuntimeError(f"{sid} {cost}x {start}~{end}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
        trades = [
            {
                "segment": f"{start.isoformat()}~{end.isoformat()}",
                "symbol": trade.get("symbol"),
                "name": trade.get("name"),
                "entry_date": trade.get("entry_date"),
                "exit_date": trade.get("exit_date"),
                "pnl_pct": trade.get("pnl_pct"),
                "pnl_amount": trade.get("pnl_amount"),
                "exit_reason": trade.get("exit_reason"),
            }
            for trade in result.trades
        ]
    frame.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
        "factor_attribution": result.factor_attribution,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }, trades


def _factor_rows() -> pl.DataFrame:
    root = DATA / "ext_data" / "alt_hs300_indminrank" / "timeseries"
    return (
        pl.scan_parquet(str(root / "date=*/part.parquet"), hive_partitioning=True)
        .select(["symbol", "date", FACTOR_COLUMN])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
        .sort(["date", "symbol"])
    )


def _coverage(membership: pl.DataFrame, factor_all: pl.DataFrame) -> dict:
    first_valid = factor_all.filter(pl.col(FACTOR_COLUMN).is_not_null())["date"].min()
    factor = factor_all.filter((pl.col("date") >= START) & (pl.col("date") <= END))
    dates = factor.select("date").unique().sort("date")["date"].to_list()
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")
    date_map = (
        pl.DataFrame({"date": dates})
        .sort("date")
        .join_asof(snapshot_dates, left_on="date", right_on="effective_date", strategy="backward")
    )
    eligible = date_map.join(membership, on="effective_date", how="inner").select(["date", "symbol"])
    eligible = eligible.join(factor, on=["date", "symbol"], how="left")
    recent_dates = dates[-WINDOW:]
    recent = eligible.filter(pl.col("date").is_in(recent_dates))
    full_valid = int(eligible[FACTOR_COLUMN].is_not_null().sum())
    recent_valid = int(recent[FACTOR_COLUMN].is_not_null().sum())
    return {
        "eligible_member_rows": eligible.height,
        "valid_member_rows": full_valid,
        "coverage_2023_2026": round(full_valid / eligible.height, 6),
        "recent_member_rows": recent.height,
        "recent_valid_rows": recent_valid,
        "recent_12m_coverage": round(recent_valid / recent.height, 6),
        "first_valid_date": str(first_valid),
        "factor_min": round(float(factor_all[FACTOR_COLUMN].min()), 6),
        "factor_p01": round(float(factor_all[FACTOR_COLUMN].quantile(0.01)), 6),
        "factor_median": round(float(factor_all[FACTOR_COLUMN].median()), 6),
        "factor_p99": round(float(factor_all[FACTOR_COLUMN].quantile(0.99)), 6),
        "factor_max": round(float(factor_all[FACTOR_COLUMN].max()), 6),
    }


def _month_end_membership(membership: pl.DataFrame, days: list[date]) -> dict[date, set[str]]:
    snapshot_dates = membership.select("effective_date").unique().sort("effective_date")["effective_date"].to_list()
    mapping = {
        key[0]: set(part["symbol"].to_list())
        for key, part in membership.group_by("effective_date", maintain_order=True)
    }
    out: dict[date, set[str]] = {}
    for day in days:
        position = bisect.bisect_right(snapshot_dates, day) - 1
        out[day] = mapping[snapshot_dates[position]] if position >= 0 else set()
    return out


def _rank_ic(frame: pl.DataFrame, left: str, right: str, minimum: int = 30) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if part.height < minimum or part[left].n_unique() < 2 or part[right].n_unique() < 2:
        return None
    ranked = part.select([
        pl.col(left).rank("average").alias("x"),
        pl.col(right).rank("average").alias("y"),
    ])
    value = ranked.select(pl.corr("x", "y")).item()
    return None if value is None else float(value)


def _eligible_rebalance_days(month_ends: list[date], calendar: list[date], start: date, end: date) -> list[date]:
    calendar_index = {day: index for index, day in enumerate(calendar)}
    return sorted({
        day for day in month_ends
        if start <= day <= end
        and calendar_index.get(day) is not None
        and calendar_index[day] + 21 < len(calendar)
        and calendar[calendar_index[day] + 21] <= end
    })

def _factor_ic(factor_all: pl.DataFrame, r166, membership: pl.DataFrame) -> dict:
    calendar = (
        pl.scan_parquet([str(path) for path in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    all_month_ends = [day for day in r166._month_ends() if START <= day <= END]
    forward = r166._forward_returns(calendar, all_month_ends)
    membership_by_day = _month_end_membership(membership, all_month_ends)
    panel = forward.join(
        factor_all.rename({"date": "rebalance_date"}),
        on=["rebalance_date", "symbol"],
        how="inner",
    )
    splits = {
        "discovery": (START, date(2024, 12, 31)),
        "validation": (date(2025, 1, 2), END),
    }
    out = {}
    for name, (start, end) in splits.items():
        allowed_days = _eligible_rebalance_days(all_month_ends, calendar, start, end)
        monthly = []
        for day in sorted(allowed_days):
            rows = panel.filter(pl.col("rebalance_date") == day)
            symbols = membership_by_day.get(day, set())
            part = rows.filter(pl.col("symbol").is_in(symbols))
            ic = _rank_ic(part, FACTOR_COLUMN, "forward_return")
            if ic is not None:
                monthly.append({"date": str(day), "ic": round(ic, 6), "names": part.height})
        values = [row["ic"] for row in monthly]
        out[name] = {
            "months": len(values),
            "mean_ic": round(sum(values) / len(values), 6) if values else None,
            "positive_rate": round(sum(value > 0 for value in values) / len(values), 4) if values else None,
            "monthly": monthly,
        }
    return out


def _independence(factor_all: pl.DataFrame, membership: pl.DataFrame, r166) -> dict:
    base = (
        pl.scan_parquet(
            [str(path) for path in sorted((DATA / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet"))],
            hive_partitioning=True,
        )
        .select(["symbol", "date", *BASE_EXT_FIELDS])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
    )
    working_capital = (
        pl.scan_parquet(
            [str(path) for path in sorted((DATA / "ext_data" / "alt_hs300_wc" / "timeseries").glob("date=*/part.parquet"))],
            hive_partitioning=True,
        )
        .select(["symbol", "date", "working_capital_change_yoy_low"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
    )
    asset_growth = (
        pl.scan_parquet(
            [str(path) for path in sorted((DATA / "ext_data" / "alt_hs300_ag" / "timeseries").glob("date=*/part.parquet"))],
            hive_partitioning=True,
        )
        .select(["symbol", "date", "asset_growth_yoy_low"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
    )
    free_cash_flow = (
        pl.scan_parquet(
            [str(path) for path in sorted((DATA / "ext_data" / "alt_hs300_fcf" / "timeseries").glob("date=*/part.parquet"))],
            hive_partitioning=True,
        )
        .select(["symbol", "date", "free_cash_flow_assets"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
    )
    gross_paths = sorted((DATA / "ext_data" / "alt_hs300_gp" / "timeseries").glob("date=*/part.parquet"))
    gross = (
        pl.scan_parquet([str(path) for path in gross_paths], hive_partitioning=True)
        .select(["symbol", "date", "gross_profitability"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
        if gross_paths
        else pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "gross_profitability": pl.Float64})
    )
    piotroski_paths = sorted((DATA / "ext_data" / "alt_hs300_pf7" / "timeseries").glob("date=*/part.parquet"))
    piotroski = (
        pl.scan_parquet([str(path) for path in piotroski_paths], hive_partitioning=True)
        .select(["symbol", "date", "piotroski7_score"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("symbol").cast(pl.String))
        if piotroski_paths
        else pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "piotroski7_score": pl.Float64})
    )
    combined = (
        factor_all.join(base, on=["symbol", "date"], how="inner")
        .join(working_capital, on=["symbol", "date"], how="left")
        .join(asset_growth, on=["symbol", "date"], how="left")
        .join(free_cash_flow, on=["symbol", "date"], how="left")
        .join(gross, on=["symbol", "date"], how="left")
        .join(piotroski, on=["symbol", "date"], how="left")
    )
    month_ends = [day for day in r166._month_ends() if START <= day <= END]
    membership_by_day = _month_end_membership(membership, month_ends)
    results = {}
    for field in CORRELATION_FIELDS:
        values = []
        for day in month_ends:
            rows = combined.filter((pl.col("date") == day) & pl.col("symbol").is_in(membership_by_day.get(day, set())))
            value = _rank_ic(rows, FACTOR_COLUMN, field)
            if value is not None:
                values.append(value)
        results[field] = {
            "months": len(values),
            "mean_abs_correlation": round(sum(abs(value) for value in values) / len(values), 6) if values else None,
            "mean_signed_correlation": round(sum(values) / len(values), 6) if values else None,
        }
    valid = [row["mean_abs_correlation"] for row in results.values() if row["mean_abs_correlation"] is not None]
    return {"fields": results, "max_mean_abs_correlation": max(valid) if valid else None}


def _pit_audit(
    factor_all: pl.DataFrame,
    builder,
    expected: pl.DataFrame | None = None,
) -> dict:
    expected_frame = (
        builder._factor_daily(builder)
        if expected is None
        else expected
    )
    actual = (
        factor_all.select(["date", "symbol", FACTOR_COLUMN])
        .with_columns(pl.lit(1, dtype=pl.Int8).alias("_actual_present"))
        .sort(["date", "symbol"])
    )
    expected_frame = expected_frame.with_columns(pl.lit(1, dtype=pl.Int8).alias("_expected_present"))
    compared = expected_frame.join(
        actual,
        on=["date", "symbol"],
        how="full",
        suffix="_actual",
    )
    actual_column = f"{FACTOR_COLUMN}_actual"
    missing_actual = int(compared["_actual_present"].is_null().sum())
    extra_actual = int(compared["_expected_present"].is_null().sum())
    difference = (
        compared.filter(pl.col(FACTOR_COLUMN).is_not_null() & pl.col(actual_column).is_not_null())
        .select((pl.col(FACTOR_COLUMN) - pl.col(actual_column)).abs().max())
        .item()
    )
    max_abs_difference = float(difference) if difference is not None else None
    formula_match = max_abs_difference is not None and max_abs_difference <= 1e-12
    valid = expected_frame.filter(pl.col(FACTOR_COLUMN).is_not_null())
    factor_min = float(valid[FACTOR_COLUMN].min()) if valid.height else None
    factor_max = float(valid[FACTOR_COLUMN].max()) if valid.height else None
    unique_pairs = valid.select(["date", FACTOR_COLUMN]).unique().height if valid.height else 0
    duplicate_rate = round(1.0 - unique_pairs / valid.height, 6) if valid.height else None
    range_valid = (
        factor_min is not None
        and factor_max is not None
        and factor_min >= 0.0
        and factor_max <= 1.0
        and bool(valid.select(pl.col(FACTOR_COLUMN).is_finite().all()).item())
    )
    return {
        "rule": "same-date percentile ranks of management expense (inverted), OCF margin and gross-margin YoY improvement within each current industry among nonfinancial historical HS300 members; min of the three percentile ranks; singleton industries use neutral 0.5; all components required",
        "rank_normalization": "for peer_count > 1: rank_pct = (peer_count - descending_average_rank) / (peer_count - 1); for peer_count = 1: rank_pct = 0.5; 1 is best and 0 is worst",
        "factor_rows_checked": actual.height,
        "expected_rows": expected_frame.height,
        "missing_written_rows": missing_actual,
        "unexpected_written_rows": extra_actual,
        "max_abs_formula_difference": max_abs_difference,
        "written_rows_match_expected_asof_panel": missing_actual == 0 and extra_actual == 0,
        "written_values_match_formula": formula_match,
        "valid_range": [factor_min, factor_max],
        "range_valid": range_valid,
        "duplicate_date_value_rate": duplicate_rate,
        "pit_unit_test": "backend/tests/test_r254_industry_min_rank_factor.py::test_r254_industry_min_rank_formula_singletons_and_missing",
        "all_checks_pass": missing_actual == 0 and extra_actual == 0 and formula_match and range_valid,
    }


def _segmented_results(service, membership, r112, r229, sid: str, cost: float) -> tuple[dict, list[dict]]:
    curves = []
    runs = []
    trades = []
    for start, end in SEGMENTS:
        curve, info, segment_trades = _run_segment(service, membership, r112, r229, sid, start, end, cost)
        curves.append(curve)
        runs.append({"start": str(start), "end": str(end), **info})
        trades.extend(segment_trades)
        print(f"{sid} {cost:g}x {start}~{end}: {info['curve_stats']}", flush=True)
        gc.collect()
    stitched = r229._stitch(curves)
    result = r229._result(r112, stitched)
    result["discovery_2023_2024"] = result.pop("discovery_2022_2024")
    return {"runs": runs, "result": result}, trades


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r166 = _load(R166_SCRIPT, "r166")
    r221 = _load(R221_SCRIPT, "r221")
    r229 = _load(R229_SCRIPT, "r229")
    r239 = _load(R239_SCRIPT, "r239")
    builder = _load(R254_BUILDER, "r254_builder")
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    universe = r221._universe_audit(all_members)
    service = r112._service()

    factor_all = _factor_rows()
    coverage = _coverage(membership, factor_all)
    pit_audit = _pit_audit(factor_all, builder)
    independence = _independence(factor_all, membership, r166)
    factor_ic = _factor_ic(factor_all, r166, membership)

    results = {}
    trades = {}
    for name, sid in STRATEGIES.items():
        result, run_trades = _segmented_results(service, membership, r112, r229, sid, 3.0)
        results[name] = result
        trades[name] = run_trades

    stress = {}
    for cost in (4.0, 5.0):
        result, _ = _segmented_results(service, membership, r112, r229, STRATEGIES["candidate"], cost)
        stress[f"{cost:g}x"] = result

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    candidate_trades = trades["candidate"]
    concentration_full = r239._aggregate(candidate_trades, industry)
    validation_trades = [
        row for row in candidate_trades
        if row["segment"].startswith("2025-01-02~")
    ]
    concentration_validation = r239._aggregate(validation_trades, industry)
    validation_positive = sum(1 for row in concentration_validation["per_symbol"] if row["pnl_amount"] > 0)

    candidate = results["candidate"]["result"]
    control = results["control"]["result"]
    d1 = coverage["coverage_2023_2026"] >= 0.99
    d2 = coverage["recent_12m_coverage"] >= 0.99
    d3 = pit_audit["all_checks_pass"]
    d4 = independence["max_mean_abs_correlation"] is not None and independence["max_mean_abs_correlation"] <= 0.70
    i1 = factor_ic["discovery"]["months"] >= 18 and (factor_ic["discovery"]["mean_ic"] or 0.0) >= 0.02
    i2 = (
        factor_ic["validation"]["months"] >= 18
        and (factor_ic["validation"]["mean_ic"] or 0.0) >= 0.01
        and (factor_ic["validation"]["mean_ic"] or 0.0) > 0
    )
    g1 = candidate["discovery_2023_2024"]["total_return"] >= control["discovery_2023_2024"]["total_return"] + 0.05
    g2 = candidate["discovery_2023_2024"]["mdd"] >= control["discovery_2023_2024"]["mdd"] - 0.05
    g3 = candidate["validation_2025_2026"]["total_return"] >= control["validation_2025_2026"]["total_return"]
    g4 = candidate["full"]["total_return"] > 0 and candidate["full"]["total_return"] >= control["full"]["total_return"]
    g5 = candidate["full"]["mdd"] >= -0.40 and candidate["full"]["mdd"] >= control["full"]["mdd"] - 0.05
    g6 = candidate["rolling_1y"]["positive_rate"] >= 0.60 and candidate["rolling_1y"]["positive_rate"] >= control["rolling_1y"]["positive_rate"] - 0.02
    g7 = candidate["recent_12m"]["total_return"] > 0 and candidate["recent_12m"]["mdd"] >= control["recent_12m"]["mdd"] - 0.05
    g8 = stress["4x"]["result"]["full"]["total_return"] > 0
    g9 = stress["5x"]["result"]["full"]["total_return"] > 0
    c1 = concentration_full["distinct_symbols"] >= 30
    c2 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top1_net_share"] <= 0.25
    c3 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top3_net_share"] <= 0.55
    c4 = concentration_full["total_pnl_amount"] > 0 and concentration_full["top5_net_share"] <= 0.75
    c5 = validation_positive >= 8
    c6 = concentration_full["top_industry_gross_share"] is not None and concentration_full["top_industry_gross_share"] <= 0.45
    c7 = concentration_full["max_symbol_loss_to_gross"] is not None and concentration_full["max_symbol_loss_to_gross"] <= 0.20
    decision = {
        "D1": d1, "D2": d2, "D3": d3, "D4": d4,
        "I1": i1, "I2": i2,
        "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5,
        "G6": g6, "G7": g7, "G8": g8, "G9": g9,
        "C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7,
    }
    decision["all_pass"] = all(decision.values())

    payload = {
        "purpose": "R254 non-financial HS300 industry-relative weakest-link quality factor versus a matched quality-cash control",
        "preregistration": (EVIDENCE / "r254_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "universe": universe,
        "factor": {
            "id": FACTOR_ID,
            "column": FACTOR_COLUMN,
            "formula": "industry_management_ocf_gm_min_rank = min(industry_rank_pct(management_expense_ratio_4q_low), industry_rank_pct(ocf_margin_4q), industry_rank_pct(gross_margin_yoy_improvement_q)); 1 is best, 0 is worst, singleton industries use 0.5",
            "pit_rule": "components inherit their existing financial PIT dates; percentile ranks and the minimum are computed only from same-date values within each current industry among historical nonfinancial HS300 members",
            "coverage": coverage,
            "pit_audit": pit_audit,
            "independence": independence,
        },
        "samples": {
            "discovery": ["2023-05-04", "2024-12-31"],
            "validation": ["2025-01-02", str(END)],
            "rolling_1y": "252-trading-day rolling compounded return over the stitched 2023-2026 curve",
            "recent_12m": "last 252 trading-day returns of the stitched 2023-2026 curve",
        },
        "execution": {
            "engine": "StrategyBacktestService",
            "matching": "open_t+1",
            "cost_3x": "6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "cost_stress": "4x and 5x scale all four components proportionally",
            "max_positions": 10,
            "score_limit": 30,
            "position_sizing": "equal",
            "holding_days": 20,
            "stop_loss": -0.2,
            "segments": [[str(start), str(end)] for start, end in SEGMENTS],
            "stitching": "two segment returns compounded into one curve; first day of each later segment skipped",
        },
        "strategies": STRATEGIES,
        "factor_ic": factor_ic,
        "results": results,
        "stress": stress,
        "concentration": {
            "full": {key: value for key, value in concentration_full.items() if key != "per_symbol"},
            "validation": {key: value for key, value in concentration_validation.items() if key != "per_symbol"},
            "validation_positive_symbols": validation_positive,
            "per_symbol_full": concentration_full["per_symbol"],
        },
        "decision": decision,
        "caveats": [
            "The weakest-link rule can become sparse when a strong component is paired with a weak one.",
            "Industry-relative ranks can be noisy in small peer groups and singleton industries receive a neutral 0.5.",
            "The current industry snapshot is not historical PIT.",
            "Management expenses do not capture selling expenses and may be classified inconsistently across firms.",
            "All components use current financial snapshots and inherit restatement risk.",
            "The discovery segment starts in 2023 to keep all three components at complete coverage.",
            "The industry snapshot used to exclude financials is current rather than historical PIT.",
            "The full curve is stitched from two system-engine segments; segment boundaries can reset an open position.",
            "Trade P&L shares are contribution diagnostics from two segments each starting at 1,000,000 notional, not a compounded-return decomposition.",
            "The factor is research-only until every preregistered gate passes; passing would justify paper-trading validation, not production deployment.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "coverage": coverage,
        "pit_audit": pit_audit,
        "factor_ic": factor_ic,
        "results": results,
        "stress": {key: value["result"]["full"] for key, value in stress.items()},
        "concentration": payload["concentration"]["full"],
        "decision": decision,
    }, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
