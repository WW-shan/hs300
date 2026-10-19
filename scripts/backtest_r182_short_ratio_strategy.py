#!/usr/bin/env python3
"""R182: low short-interest crowding long-only strategy backtest."""
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
OUT = EVIDENCE / "r182_short_ratio_strategy.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
MARGIN_DIR = DATA / "research" / "r50_margin"
START = date(2021, 6, 1)
END = date(2026, 8, 31)
VALIDATION_START = date(2024, 1, 1)
MIN_NAMES = 60
QUANTILE = 0.30
COSTS = {"base": 0.003, "stress": 0.006}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def _snapshots() -> pl.DataFrame:
    files = sorted(MARGIN_DIR.glob("*.parquet"))
    inst = (
        pl.read_parquet(DATA / "instruments" / "instruments.parquet")
        .select(["symbol", "float_shares"])
        .unique(subset=["symbol"])
    )
    frame = (
        pl.concat([pl.read_parquet(f) for f in files])
        .select(["symbol", "snapshot_date", "short_qty"])
        .unique()
        .sort(["symbol", "snapshot_date"])
    )
    frame = frame.join(inst, on="symbol", how="left").with_columns(
        pl.when((pl.col("float_shares") > 0) & (pl.col("short_qty") >= 0))
        .then(pl.col("short_qty") / pl.col("float_shares")).otherwise(None).alias("short_ratio"),
        pl.col("snapshot_date").dt.offset_by("1d").alias("available_date"),
    )
    return frame.select(["symbol", "available_date", "short_ratio"])


def _month_returns(
    calendar: list[date],
    month_ends: list[date],
    prices: pl.DataFrame,
    snaps: pl.DataFrame,
    daily_dates: list[date],
    daily_map: dict[date, set[str]],
    monthly_dates: list[date],
    monthly_map: dict[date, set[str]],
    r167,
) -> list[dict]:
    index = {day: i for i, day in enumerate(calendar)}
    records = []
    for day in month_ends:
        pos = index.get(day)
        if pos is None or pos + 21 >= len(calendar):
            continue
        entry_date = calendar[pos + 1]
        exit_date = calendar[pos + 21]
        if daily_dates and day >= daily_dates[0]:
            members = r167._members_on(daily_dates, daily_map, day)
        else:
            members = r167._members_on(monthly_dates, monthly_map, day)
        if not members:
            continue
        base = pl.DataFrame({"symbol": sorted(members), "date": [day] * len(members)})
        joined = base.join_asof(
            snaps.sort(["symbol", "available_date"]),
            left_on="date", right_on="available_date", by="symbol", strategy="backward", check_sortedness=False,
        ).with_columns((pl.col("date") - pl.col("available_date")).dt.total_days().alias("age_days"))
        joined = joined.with_columns(
            pl.when(pl.col("age_days").is_between(0, 45, closed="both"))
            .then(pl.col("short_ratio")).otherwise(None).alias("short_ratio")
        ).with_columns([
            pl.col("short_ratio").quantile(0.01).alias("_lo"),
            pl.col("short_ratio").quantile(0.99).alias("_hi"),
        ]).with_columns(
            pl.col("short_ratio").clip(pl.col("_lo"), pl.col("_hi")).alias("short_ratio")
        ).drop(["_lo", "_hi"])
        entry = prices.filter(pl.col("date") == entry_date).select(["symbol", pl.col("open").alias("entry_open")])
        exit_ = prices.filter(pl.col("date") == exit_date).select(["symbol", pl.col("open").alias("exit_open")])
        returns = (
            joined.join(entry, on="symbol", how="inner").join(exit_, on="symbol", how="inner")
            .with_columns((pl.col("exit_open") / pl.col("entry_open") - 1.0).alias("forward_return"))
            .filter(pl.col("forward_return").is_not_null() & pl.col("forward_return").is_finite())
        )
        valid = returns.filter(pl.col("short_ratio").is_not_null())
        universe_return = returns["forward_return"].mean()
        if valid.height < MIN_NAMES:
            records.append({
                "date": str(day), "entry_date": str(entry_date), "exit_date": str(exit_date),
                "members": len(members), "valid": valid.height, "coverage": 0.0,
                "portfolio_return": None, "universe_return": float(universe_return) if universe_return is not None else None,
                "selected": 0,
            })
            continue
        ordered = valid.sort("short_ratio")
        cut = max(MIN_NAMES, int(ordered.height * QUANTILE))
        selected = ordered.head(cut)
        portfolio_return = selected["forward_return"].mean()
        records.append({
            "date": str(day), "entry_date": str(entry_date), "exit_date": str(exit_date),
            "members": len(members), "valid": valid.height,
            "coverage": round(valid.height / len(members), 4) if members else 0.0,
            "portfolio_return": float(portfolio_return) if portfolio_return is not None else None,
            "universe_return": float(universe_return) if universe_return is not None else None,
            "selected": cut,
        })
    return records


def _net(values: list[float], cost: float, n: int) -> list[float]:
    return [v - cost for v in values]


def _stats(values: list[float]) -> dict:
    if not values:
        return {"months": 0, "mean": None, "positive_rate": None, "worst": None}
    return {
        "months": len(values),
        "mean": round(st.mean(values), 6),
        "positive_rate": round(sum(v > 0 for v in values) / len(values), 4),
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


def _annualized(values: list[float]) -> float | None:
    if not values:
        return None
    total = _compound(values)
    years = len(values) / 12.0
    if years <= 0 or total <= -1.0:
        return None
    return (1.0 + total) ** (1.0 / years) - 1.0


def _rolling_12m(values: list[float]) -> list[float]:
    if len(values) < 12:
        return []
    return [_compound(values[i:i + 12]) for i in range(0, len(values) - 11)]


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if START <= day <= END]
    prices = r167._prices()
    snaps = _snapshots()
    daily_dates, daily_map = r167._membership()
    monthly_dates, monthly_map = _monthly_membership()
    records = _month_returns(calendar, month_ends, prices, snaps, daily_dates, daily_map, monthly_dates, monthly_map, r167)

    valid_records = [r for r in records if r["portfolio_return"] is not None]
    port = [r["portfolio_return"] for r in valid_records]
    univ = [r["universe_return"] for r in valid_records]
    net_base = _net(port, COSTS["base"], len(port))
    net_stress = _net(port, COSTS["stress"], len(port))
    net_univ = _net(univ, COSTS["base"], len(univ))

    validation = [r for r in valid_records if date.fromisoformat(r["date"]) >= VALIDATION_START]
    val_port = _net([r["portfolio_return"] for r in validation], COSTS["base"], len(validation))
    val_univ = _net([r["universe_return"] for r in validation], COSTS["base"], len(validation))

    rolling = _rolling_12m(net_base)
    recent_12 = _compound(net_base[-12:]) if len(net_base) >= 12 else None
    avg_coverage = st.mean(r["coverage"] for r in valid_records) if valid_records else None
    min_valid = min((r["valid"] for r in valid_records), default=0)

    d1 = bool(len(valid_records) >= 50 and min_valid >= MIN_NAMES and avg_coverage is not None and avg_coverage >= 0.90)
    g1 = bool(_compound(net_base) > _compound(net_univ) and (_annualized(net_base) or -1) > 0)
    g2 = bool(rolling and sum(v > 0 for v in rolling) / len(rolling) >= 0.75 and min(rolling) >= -0.15)
    g3 = bool(recent_12 is not None and recent_12 > 0)
    g4 = bool(_mdd(net_base) >= -0.35 and _mdd(net_base) >= _mdd(net_univ))
    g5 = bool(_compound(net_stress) > 0)
    g6 = bool(_compound(val_port) > 0 and _compound(val_port) > _compound(val_univ))
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R182 low short-interest crowding long-only strategy",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "snapshots": "data/research/r50_margin, 2021-04..2026-09",
            "months": len(records),
            "valid_months": len(valid_records),
            "start": records[0]["date"] if records else None,
            "end": records[-1]["date"] if records else None,
        },
        "method": {
            "factor": "short_qty / float_shares (current instruments), low is good",
            "selection": "bottom 30% equal weight, monthly rebalance",
            "execution": "T+1 open to T+21 open",
            "costs": {"base": COSTS["base"], "stress": COSTS["stress"]},
            "benchmark": "equal-weight all historical members, same execution and base cost",
            "splits": {"validation_start": str(VALIDATION_START)},
        },
        "summary": {
            "portfolio": {
                "gross": _stats(port),
                "net_base": _stats(net_base),
                "net_stress": _stats(net_stress),
                "compound_net_base": round(_compound(net_base), 6),
                "compound_net_stress": round(_compound(net_stress), 6),
                "annualized_net_base": round(_annualized(net_base), 6) if _annualized(net_base) is not None else None,
                "mdd_net_base": round(_mdd(net_base), 6),
                "rolling_12m": {
                    "windows": len(rolling),
                    "positive_rate": round(sum(v > 0 for v in rolling) / len(rolling), 4) if rolling else None,
                    "worst": round(min(rolling), 6) if rolling else None,
                    "median": round(st.median(rolling), 6) if rolling else None,
                },
                "recent_12m": round(recent_12, 6) if recent_12 is not None else None,
                "avg_coverage": round(avg_coverage, 4) if avg_coverage is not None else None,
                "min_valid": min_valid,
            },
            "benchmark": {
                "net_base": _stats(net_univ),
                "compound_net_base": round(_compound(net_univ), 6),
                "annualized_net_base": round(_annualized(net_univ), 6) if _annualized(net_univ) is not None else None,
                "mdd_net_base": round(_mdd(net_univ), 6),
            },
            "validation": {
                "months": len(validation),
                "portfolio_compound": round(_compound(val_port), 6),
                "benchmark_compound": round(_compound(val_univ), 6),
            },
        },
        "decision": decision,
        "monthly_records": records,
        "limitations": [
            "float_shares uses the current instruments snapshot, not PIT historical share counts.",
            "Margin snapshots only cover 2021-04 onwards.",
            "The strategy holds cash when fewer than 60 names have valid short_ratio.",
            "Monthly 20-day forward returns overlap slightly at boundaries.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": result["summary"], "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
