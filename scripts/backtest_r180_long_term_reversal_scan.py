#!/usr/bin/env python3
"""R180: long-term reversal (250d) with low-liquidity interaction scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r180_long_term_reversal_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2019, 7, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
MIN_NAMES = 60
MIN_GROUP = 30
MIN_LEG = 20
COST = 0.003
LOOKBACK = 250
LIQ_WINDOW = 20
FACTOR = "long_term_reversal"
CONTROL = "momentum_60d"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _prices() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date", "open", "close", "amount"])
        .collect()
        .sort(["symbol", "date"])
    )


def _monthly_membership() -> tuple[list[date], dict[date, set[str]]]:
    frame = (
        pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet")
        .select([pl.col("query_date").cast(pl.Date).alias("snapshot_date"), pl.col("symbol")])
        .unique()
        .sort(["snapshot_date", "symbol"])
    )
    dates = sorted(frame["snapshot_date"].unique().to_list())
    mapping = {}
    for (snapshot,), part in frame.group_by(["snapshot_date"], maintain_order=True):
        mapping[snapshot] = set(part["symbol"].to_list())
    return dates, mapping


def _factor_frame(prices: pl.DataFrame) -> pl.DataFrame:
    return (
        prices.with_columns(
            (pl.col("close") / pl.col("close").shift(LOOKBACK).over("symbol") - 1.0).alias("_ret_250d"),
            (pl.col("close") / pl.col("close").shift(60).over("symbol") - 1.0).alias(CONTROL),
            pl.col("amount").rolling_mean(window_size=LIQ_WINDOW, min_samples=LIQ_WINDOW).over("symbol").alias("amount_mean_20d"),
        )
        .with_columns((-pl.col("_ret_250d")).alias(FACTOR))
        .select(["symbol", "date", FACTOR, CONTROL, "amount_mean_20d"])
        .filter(pl.col(FACTOR).is_not_null() & pl.col(FACTOR).is_finite())
    )


def _rank_corr(frame: pl.DataFrame, left: str, right: str, min_names: int = MIN_NAMES) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if part.height < min_names or part[left].n_unique() < 2 or part[right].n_unique() < 2:
        return None
    x = part[left].rank("average")
    y = part[right].rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(frame: pl.DataFrame) -> float | None:
    part = frame.select([FACTOR, CONTROL, "forward_return"]).drop_nulls()
    n = part.height
    if n < MIN_NAMES or part[FACTOR].n_unique() < 2 or part[CONTROL].n_unique() < 2:
        return None
    xs = part[CONTROL].to_list()
    ys = part[FACTOR].to_list()
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    residual = part.with_columns((pl.col(FACTOR) - alpha - beta * pl.col(CONTROL)).alias("_resid"))
    return _rank_corr(residual, "_resid", "forward_return")


def _long_short(joined: pl.DataFrame) -> tuple[float, int] | None:
    part = joined.select([FACTOR, "forward_return"]).drop_nulls()
    if part.height < 2 * MIN_LEG:
        return None
    part = part.sort(FACTOR, descending=True)
    cut = max(MIN_LEG, int(part.height * 0.30))
    top = part.head(cut)["forward_return"].mean()
    bottom = part.tail(cut)["forward_return"].mean()
    if top is None or bottom is None:
        return None
    return float(top - bottom - COST), part.height


def _liquidity_ic(joined: pl.DataFrame) -> tuple[float | None, float | None, int, int]:
    part = joined.select([FACTOR, "amount_mean_20d", "forward_return"]).drop_nulls()
    if part.height < 2 * MIN_GROUP:
        return None, None, 0, 0
    median = part["amount_mean_20d"].median()
    if median is None:
        return None, None, 0, 0
    low = part.filter(pl.col("amount_mean_20d") <= median)
    high = part.filter(pl.col("amount_mean_20d") > median)
    if low.height < MIN_GROUP or high.height < MIN_GROUP:
        return None, None, low.height, high.height
    return (
        _rank_corr(low, FACTOR, "forward_return", MIN_GROUP),
        _rank_corr(high, FACTOR, "forward_return", MIN_GROUP),
        low.height,
        high.height,
    )


def _stats(values: list[float]) -> dict:
    if not values:
        return {"months": 0, "mean": None, "positive_rate": None, "p05": None, "worst": None}
    ordered = sorted(values)
    return {
        "months": len(values),
        "mean": round(st.mean(values), 6),
        "positive_rate": round(sum(v > 0 for v in values) / len(values), 4),
        "p05": round(ordered[max(0, int(len(values) * 0.05))], 6),
        "worst": round(min(values), 6),
    }


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
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    prices = _prices()
    factors = _factor_frame(prices)
    forward = r167._forward_returns(
        calendar,
        month_ends,
        prices.select(["symbol", "date", "open"]).with_columns(pl.col("open").alias("raw_close")),
    )
    daily_dates, daily_map = r167._membership()
    monthly_dates, monthly_map = _monthly_membership()

    records = []
    for day in month_ends:
        if daily_dates and day >= daily_dates[0]:
            members = r167._members_on(daily_dates, daily_map, day)
            source = "daily"
        else:
            members = r167._members_on(monthly_dates, monthly_map, day)
            source = "monthly"
        if not members:
            continue
        base = pl.DataFrame({"symbol": sorted(members)})
        f = factors.filter(pl.col("date") == day)
        joined = base.join(f, on="symbol", how="left")
        joined = joined.join(
            forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"]),
            on="symbol",
            how="inner",
        )
        factor_names = int(joined[FACTOR].is_not_null().sum())
        coverage = factor_names / base.height if base.height else 0.0
        ic = _rank_corr(joined, FACTOR, "forward_return")
        control_corr = _rank_corr(joined, FACTOR, CONTROL)
        residual_ic = _residual_ic(joined)
        ls = _long_short(joined)
        ic_low, ic_high, low_n, high_n = _liquidity_ic(joined)
        ic_diff = ic_low - ic_high if ic_low is not None and ic_high is not None else None
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        records.append({
            "date": str(day),
            "split": split,
            "membership_source": source,
            "members": base.height,
            "factor_names": factor_names,
            "coverage": round(coverage, 4),
            "ic": round(ic, 6) if ic is not None else None,
            "momentum_corr": round(control_corr, 6) if control_corr is not None else None,
            "residual_ic": round(residual_ic, 6) if residual_ic is not None else None,
            "ic_low_liquidity": round(ic_low, 6) if ic_low is not None else None,
            "ic_high_liquidity": round(ic_high, 6) if ic_high is not None else None,
            "ic_liquidity_diff": round(ic_diff, 6) if ic_diff is not None else None,
            "low_names": low_n,
            "high_names": high_n,
            "long_short_net": round(ls[0], 6) if ls is not None else None,
            "long_short_names": ls[1] if ls is not None else None,
        })

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [row for row in records if row["split"] == split]
        ic_rows = [row for row in part if row["ic"] is not None]
        ls_values = [row["long_short_net"] for row in part if row["long_short_net"] is not None]
        ic_values = [float(row["ic"]) for row in ic_rows]
        diff_rows = [row for row in ic_rows if row["ic_liquidity_diff"] is not None]
        diff_values = [float(row["ic_liquidity_diff"]) for row in diff_rows]
        summary[split] = {
            "months": len(part),
            "ic_months": len(ic_rows),
            "ic_mean": round(st.mean(ic_values), 6) if ic_values else None,
            "ic_win_rate": round(sum(v > 0 for v in ic_values) / len(ic_values), 4) if ic_values else None,
            "coverage_mean": round(st.mean(row["coverage"] for row in part), 4) if part else None,
            "min_factor_names": min((row["factor_names"] for row in part), default=None),
            "momentum_corr_mean": round(st.mean(row["momentum_corr"] for row in ic_rows if row["momentum_corr"] is not None), 4) if any(row["momentum_corr"] is not None for row in ic_rows) else None,
            "residual_ic_mean": round(st.mean(row["residual_ic"] for row in ic_rows if row["residual_ic"] is not None), 6) if any(row["residual_ic"] is not None for row in ic_rows) else None,
            "ic_low_mean": round(st.mean(row["ic_low_liquidity"] for row in diff_rows), 6) if diff_rows else None,
            "ic_high_mean": round(st.mean(row["ic_high_liquidity"] for row in diff_rows), 6) if diff_rows else None,
            "ic_diff_mean": round(st.mean(diff_values), 6) if diff_values else None,
            "ic_diff_months": len(diff_values),
            "long_short": _stats(ls_values),
            "long_short_compound": round(_compound(ls_values), 6) if ls_values else None,
            "long_short_mdd": round(_mdd(ls_values), 6) if ls_values else None,
        }

    recent_ls = [row["long_short_net"] for row in records if row["split"] == "holdout" and row["long_short_net"] is not None][-12:]
    recent_12m = round(_compound(recent_ls), 6) if recent_ls else None

    d1 = all(
        summary[s]["months"] >= threshold
        and summary[s]["coverage_mean"] is not None
        and summary[s]["coverage_mean"] >= 0.80
        and summary[s]["min_factor_names"] is not None
        and summary[s]["min_factor_names"] >= MIN_NAMES
        for s, threshold in [("discovery", 36), ("validation", 24), ("holdout", 18)]
    )
    g1 = summary["discovery"]["ic_mean"] is not None and summary["discovery"]["ic_mean"] >= 0.030 and summary["discovery"]["ic_win_rate"] >= 0.55
    g2 = summary["validation"]["ic_mean"] is not None and summary["validation"]["ic_mean"] >= 0.015 and summary["validation"]["ic_win_rate"] >= 0.52
    g3 = summary["holdout"]["ic_mean"] is not None and summary["holdout"]["ic_mean"] >= 0.010 and summary["holdout"]["ic_win_rate"] >= 0.52 and recent_12m is not None and recent_12m > 0
    g4 = all(
        summary[s]["long_short"]["mean"] is not None
        and summary[s]["long_short"]["mean"] > 0
        and summary[s]["long_short"]["positive_rate"] >= 0.52
        for s in ["validation", "holdout"]
    ) and summary["holdout"]["long_short_mdd"] is not None and summary["holdout"]["long_short_mdd"] >= -0.20
    g5 = (
        summary["validation"]["momentum_corr_mean"] is not None
        and abs(summary["validation"]["momentum_corr_mean"]) < 0.70
        and summary["validation"]["residual_ic_mean"] is not None
        and summary["validation"]["residual_ic_mean"] >= 0.010
    )
    g6 = (
        summary["discovery"]["ic_diff_mean"] is not None
        and summary["discovery"]["ic_diff_mean"] >= 0.020
        and summary["validation"]["ic_diff_mean"] is not None
        and summary["validation"]["ic_diff_mean"] > 0
    )
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R180 long-term reversal (250d) with low-liquidity interaction",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "prices": "data/kline_daily_enriched",
            "membership": "monthly r157 before 2020-12-31; daily r78 thereafter",
            "price_rows": prices.height,
            "price_symbols": prices["symbol"].n_unique(),
            "price_start": str(prices["date"].min()),
            "price_end": str(prices["date"].max()),
            "missing_close": int(prices["close"].is_null().sum()),
            "missing_amount": int(prices["amount"].is_null().sum()),
            "factor_rows": factors.height,
        },
        "method": {
            "factor": "long_term_reversal = -(close_T / close_{T-250} - 1)",
            "direction": "positive",
            "liquidity_proxy": "amount_mean_20d = rolling mean of amount over past 20 trading days",
            "liquidity_split": "monthly median split into low (<= median) and high (> median); IC_low - IC_high",
            "control": "momentum_60d = close_T / close_{T-60} - 1",
            "forward_return": "T+1 open to T+21 open",
            "ic": "monthly cross-sectional Spearman rank IC",
            "long_short": "top 30% minus bottom 30%, monthly, 0.30% round-trip cost",
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_short": recent_12m,
        "decision": decision,
        "monthly_records": records,
        "limitations": [
            "Monthly 20-day forward returns overlap slightly at boundaries.",
            "Liquidity subgroup ICs require at least 30 names per group; months failing that are excluded from the interaction test.",
            "The liquidity proxy (past 20-day amount) is a size/turnover mixture, not a pure free-float measure.",
            "Industry/status changes are not modeled beyond HS300 membership snapshots.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_short": recent_12m, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
