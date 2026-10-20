#!/usr/bin/env python3
"""R242: system-engine test of the non-financial HS300 working-capital factor."""
from __future__ import annotations

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
OUT = EVIDENCE / "r242_working_capital_change.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R166_SCRIPT = ROOT / "scripts" / "backtest_r166_weak_regime_factor_scan.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
CACHE = DATA / "research" / "r242_cache"
START = date(2022, 7, 1)
END = date(2026, 10, 16)
WINDOW = 252
SEGMENTS = [
    (date(2022, 7, 1), date(2024, 12, 31)),
    (date(2025, 1, 2), END),
]
STRATEGIES = {
    "candidate": "ai_hs300_working_capital_change_quality_h20_sl20",
    "control": "ai_hs300_working_capital_change_quality_control",
}
FACTOR_ID = "ext_alt_hs300_wc_working_capital_change_yoy_low"
FACTOR_COLUMN = "working_capital_change_yoy_low"
BASE_EXT_FIELDS = (
    "ocf_margin_4q",
    "cash_to_assets",
    "sga_ratio_4q_low",
    "cash_flow_stability_4q_low",
    "goodwill_ratio_low",
    "capex_intensity_low",
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
        if "未产生买入信号" in str(result.error):
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
    root = DATA / "ext_data" / "alt_hs300_wc" / "timeseries"
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
        "coverage_2022_2026": round(full_valid / eligible.height, 6),
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


def _month_end_membership(r166, days: list[date]) -> dict[date, set[str]]:
    snapshot_dates, mapping = r166._membership()
    return {day: r166._snapshot_symbols(snapshot_dates, mapping, day) for day in days}


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

def _factor_ic(factor_all: pl.DataFrame, r166) -> dict:
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
    membership_by_day = _month_end_membership(r166, all_month_ends)
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
    combined = factor_all.join(base, on=["symbol", "date"], how="inner")
    month_ends = [day for day in r166._month_ends() if START <= day <= END]
    membership_by_day = _month_end_membership(r166, month_ends)
    results = {}
    for field in BASE_EXT_FIELDS:
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
    return {"runs": runs, "result": r229._result(r112, stitched)}, trades


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r166 = _load(R166_SCRIPT, "r166")
    r221 = _load(R221_SCRIPT, "r221")
    r229 = _load(R229_SCRIPT, "r229")
    r239 = _load(R239_SCRIPT, "r239")
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    universe = r221._universe_audit(all_members)
    service = r112._service()

    factor_all = _factor_rows()
    coverage = _coverage(membership, factor_all)
    independence = _independence(factor_all, membership, r166)
    factor_ic = _factor_ic(factor_all, r166)

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
    d1 = coverage["coverage_2022_2026"] >= 0.90
    d2 = coverage["recent_12m_coverage"] >= 0.95
    d3 = coverage["first_valid_date"] <= str(START) and coverage["first_valid_date"] >= "2022-05-01"
    d4 = independence["max_mean_abs_correlation"] is not None and independence["max_mean_abs_correlation"] <= 0.70
    i1 = factor_ic["discovery"]["months"] >= 24 and (factor_ic["discovery"]["mean_ic"] or 0.0) >= 0.02
    i2 = factor_ic["validation"]["months"] >= 18 and (factor_ic["validation"]["mean_ic"] or 0.0) >= 0.01
    g1 = candidate["discovery_2022_2024"]["total_return"] >= control["discovery_2022_2024"]["total_return"] + 0.05
    g2 = candidate["discovery_2022_2024"]["mdd"] >= control["discovery_2022_2024"]["mdd"] - 0.05
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
        "purpose": "R242 non-financial HS300 working-capital year-over-year improvement versus a matched quality-cash control",
        "preregistration": (EVIDENCE / "r242_preregistration.json").relative_to(ROOT).as_posix(),
        "window": [str(START), str(END)],
        "universe": universe,
        "factor": {
            "id": FACTOR_ID,
            "column": FACTOR_COLUMN,
            "formula": "clip(NWC_ratio_(t-4) - NWC_ratio_t, -1, 1), where NWC = accounts_receivable + inventory - accounts_payable",
            "effective_date": "current report statutory disclosure deadline + 3 calendar days",
            "coverage": coverage,
            "independence": independence,
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
            "The clean point-in-time financial sample starts in 2022; 2018-2020 financials are excluded because reliable first-disclosure dates are unavailable.",
            "The factor uses statutory disclosure deadlines as a fallback for first disclosure time.",
            "Local financial tables are current snapshots, so restatement risk remains.",
            "The factor excludes advance receipts because their coverage is only 32.99%; this avoids a structural missingness regime.",
            "The industry snapshot used to exclude financials is current rather than historical PIT.",
            "The full curve is stitched from two system-engine segments; segment boundaries can reset an open position.",
            "Trade P&L shares are contribution diagnostics from two segments each starting at 1,000,000 notional, not a compounded-return decomposition.",
            "The factor is research-only until every preregistered gate passes; passing would justify paper-trading validation, not production deployment.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"coverage": coverage, "factor_ic": factor_ic, "results": results, "stress": {key: value["result"]["full"] for key, value in stress.items()}, "concentration": payload["concentration"]["full"], "decision": decision}, ensure_ascii=False, indent=2), flush=True)
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())