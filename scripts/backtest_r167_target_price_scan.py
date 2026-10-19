#!/usr/bin/env python3
"""R167: analyst target-price factor scan (upside / revision / dispersion)."""
from __future__ import annotations

import bisect
import json
import math
import statistics as st
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r167_target_price_scan.json"
ANALYST_DIR = DATA / "research" / "r47_analyst"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
DISCOVERY_START = date(2022, 1, 1)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
VALIDATION_END = date(2026, 9, 30)
MIN_NAMES = 30
MIN_LEG = 10
COST = 0.003
FACTORS = ["tp_upside", "tp_revision_90d", "tp_dispersion_low"]


def _calendar() -> list[date]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )


def _month_ends(calendar: list[date]) -> list[date]:
    frame = pl.DataFrame({"date": calendar}).with_columns([
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    ])
    return (
        frame.group_by(["year", "month"])
        .agg(pl.col("date").max())
        .sort(["year", "month"])["date"]
        .to_list()
    )


def _prices() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date", "open", "raw_close"])
        .collect()
        .sort(["symbol", "date"])
    )


def _reports() -> pl.DataFrame:
    files = sorted(ANALYST_DIR.glob("*.parquet"))
    raw = (
        pl.scan_parquet([str(path) for path in files])
        .select(["symbol", "report_date", "target_low", "target_high"])
        .collect()
        .with_columns(pl.col("report_date").cast(pl.Date))
    )
    return (
        raw.with_columns(((pl.col("target_low") + pl.col("target_high")) / 2.0).alias("target_mid"))
        .filter(pl.col("target_mid").is_not_null() & (pl.col("target_mid") > 0))
        .sort(["symbol", "report_date"])
    )


def _membership() -> tuple[list[date], dict[date, set[str]]]:
    frame = (
        pl.read_parquet(MEMBERSHIP)
        .select([pl.col("query_date").cast(pl.Date).alias("snapshot_date"), pl.col("symbol")])
        .unique()
        .sort(["snapshot_date", "symbol"])
    )
    dates = sorted(frame["snapshot_date"].unique().to_list())
    mapping = {}
    for (snapshot,), part in frame.group_by(["snapshot_date"], maintain_order=True):
        mapping[snapshot] = set(part["symbol"].to_list())
    return dates, mapping


def _members_on(snapshot_dates: list[date], mapping: dict[date, set[str]], day: date) -> set[str]:
    pos = bisect.bisect_right(snapshot_dates, day) - 1
    return mapping[snapshot_dates[pos]] if pos >= 0 else set()


def _forward_returns(calendar: list[date], month_ends: list[date], prices: pl.DataFrame) -> pl.DataFrame:
    index = {day: i for i, day in enumerate(calendar)}
    rows = []
    for day in month_ends:
        pos = index.get(day)
        if pos is None or pos + 21 >= len(calendar):
            continue
        rows.append({
            "rebalance_date": day,
            "entry_date": calendar[pos + 1],
            "exit_date": calendar[pos + 21],
        })
    mapping = pl.DataFrame(rows, schema={
        "rebalance_date": pl.Date,
        "entry_date": pl.Date,
        "exit_date": pl.Date,
    })
    entry = (
        prices.join(mapping.select(["rebalance_date", "entry_date"]), left_on="date", right_on="entry_date", how="inner")
        .select(["rebalance_date", "symbol", pl.col("open").alias("entry_open")])
    )
    exit_ = (
        prices.join(mapping.select(["rebalance_date", "exit_date"]), left_on="date", right_on="exit_date", how="inner")
        .select(["rebalance_date", "symbol", pl.col("open").alias("exit_open")])
    )
    return (
        entry.join(exit_, on=["rebalance_date", "symbol"], how="inner")
        .with_columns((pl.col("exit_open") / pl.col("entry_open") - 1.0).alias("forward_return"))
        .select(["rebalance_date", "symbol", "forward_return"])
        .filter(pl.col("forward_return").is_not_null() & pl.col("forward_return").is_finite())
    )


def _rank_ic(frame: pl.DataFrame, factor: str) -> float | None:
    part = frame.select([factor, "forward_return"]).drop_nulls()
    if part.height < MIN_NAMES or part[factor].n_unique() < 2:
        return None
    x = part[factor].rank("average")
    y = part["forward_return"].rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _month_factor_frame(day: date, members: set[str], reports: pl.DataFrame, ext: pl.DataFrame) -> pl.DataFrame:
    recent = reports.filter(
        (pl.col("report_date") <= day)
        & (pl.col("report_date") > day - timedelta(days=180))
        & pl.col("symbol").is_in(members)
    )
    if recent.is_empty():
        return pl.DataFrame()
    last90 = recent.filter(pl.col("report_date") > day - timedelta(days=90))
    latest = (
        last90.sort(["symbol", "report_date"])
        .group_by("symbol")
        .agg([pl.col("tp_upside").last().alias("tp_upside")])
    )
    rev90 = last90.group_by("symbol").agg(pl.col("target_mid").median().alias("mid_90"))
    prev = (
        recent.filter(pl.col("report_date") <= day - timedelta(days=90))
        .group_by("symbol")
        .agg(pl.col("target_mid").median().alias("mid_prev"))
    )
    revision = (
        rev90.join(prev, on="symbol", how="inner")
        .with_columns((pl.col("mid_90") / pl.col("mid_prev") - 1.0).alias("tp_revision_90d"))
        .select(["symbol", "tp_revision_90d"])
    )
    dispersion = (
        recent.group_by("symbol")
        .agg([
            pl.col("target_mid").std().alias("_std"),
            pl.col("target_mid").mean().alias("_mean"),
            pl.len().alias("_n"),
        ])
        .filter((pl.col("_n") >= 2) & (pl.col("_mean") > 0))
        .with_columns((-pl.col("_std") / pl.col("_mean")).alias("tp_dispersion_low"))
        .select(["symbol", "tp_dispersion_low"])
    )
    out = latest.join(revision, on="symbol", how="left").join(dispersion, on="symbol", how="left")
    return out.join(ext.select(["symbol", "analyst_rating_score_20d"]), on="symbol", how="left").select(["symbol", *FACTORS, "analyst_rating_score_20d"])


def _regime(day: date, index_values: list[float], index_pos: dict[date, int]) -> str | None:
    pos = index_pos.get(day)
    if pos is None or pos < 60:
        return None
    return "weak" if index_values[pos] / index_values[pos - 60] - 1.0 <= 0 else "strong"


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def main() -> int:
    calendar = _calendar()
    month_ends = [day for day in _month_ends(calendar) if DISCOVERY_START <= day <= VALIDATION_END]
    prices = _prices()
    reports = _reports()
    reports = (
        reports.join_asof(
            prices.select(["symbol", "date", "raw_close"]).rename({"date": "report_date"}),
            on="report_date",
            by="symbol",
            strategy="backward",
        )
        .filter(pl.col("raw_close").is_not_null() & (pl.col("raw_close") > 0))
        .with_columns((pl.col("target_mid") / pl.col("raw_close") - 1.0).alias("tp_upside"))
    )
    snapshot_dates, membership_map = _membership()
    forward = _forward_returns(calendar, month_ends, prices)
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_values = index["close"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}

    records = []
    for day in month_ends:
        members = _members_on(snapshot_dates, membership_map, day)
        if len(members) < MIN_NAMES:
            continue
        ext_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
        if not ext_path.exists():
            continue
        ext = pl.read_parquet(ext_path).select(["symbol", "analyst_rating_score_20d"])
        factors = _month_factor_frame(day, members, reports, ext)
        if factors.is_empty():
            continue
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        joined = factors.join(fwd, on="symbol", how="inner")
        if joined.height < MIN_NAMES:
            continue
        regime = _regime(day, index_values, index_pos)
        split = "discovery" if day <= DISCOVERY_END else "validation"
        for factor in FACTORS:
            non_null = joined[factor].is_not_null().sum()
            ic = _rank_ic(joined, factor)
            records.append({
                "date": day.isoformat(),
                "split": split,
                "regime": regime,
                "factor": factor,
                "ic": ic,
                "coverage": round(float(non_null) / len(members), 6),
                "n": int(non_null),
            })

    summaries = []
    for factor in FACTORS:
        part = [row for row in records if row["factor"] == factor]
        disc = [row for row in part if row["split"] == "discovery" and row["ic"] is not None]
        val = [row for row in part if row["split"] == "validation" and row["ic"] is not None]
        val_weak = [row for row in val if row["regime"] == "weak"]
        if not disc:
            continue
        disc_ic = st.mean(row["ic"] for row in disc)
        orientation = 1.0 if disc_ic >= 0 else -1.0
        summaries.append({
            "factor": factor,
            "orientation": orientation,
            "discovery_ic": round(disc_ic, 6),
            "discovery_abs_ic": round(abs(disc_ic), 6),
            "discovery_win_rate": round(sum(row["ic"] * orientation > 0 for row in disc) / len(disc), 4),
            "discovery_coverage": round(st.mean(row["coverage"] for row in disc), 4),
            "validation_ic_oriented": round(st.mean(row["ic"] * orientation for row in val), 6) if val else None,
            "validation_win_rate": round(sum(row["ic"] * orientation > 0 for row in val) / len(val), 4) if val else None,
            "validation_coverage": round(st.mean(row["coverage"] for row in val), 4) if val else None,
            "validation_weak_ic": round(st.mean(row["ic"] * orientation for row in val_weak), 6) if val_weak else None,
            "validation_weak_months": len(val_weak),
            "discovery_months": len(disc),
            "validation_months": len(val),
        })

    rating_corr = {}
    for factor in FACTORS:
        values = []
        for day in month_ends:
            members = _members_on(snapshot_dates, membership_map, day)
            if len(members) < MIN_NAMES:
                continue
            ext_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
            if not ext_path.exists():
                continue
            ext = pl.read_parquet(ext_path).select(["symbol", "analyst_rating_score_20d"])
            factors = _month_factor_frame(day, members, reports, ext)
            if factors.is_empty():
                continue
            part = factors.select([factor, "analyst_rating_score_20d"]).drop_nulls()
            if part.height < MIN_NAMES or part[factor].n_unique() < 2 or part["analyst_rating_score_20d"].n_unique() < 2:
                continue
            x = part[factor].rank("average")
            y = part["analyst_rating_score_20d"].rank("average")
            value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
            if value is not None and math.isfinite(float(value)):
                values.append(abs(float(value)))
        rating_corr[factor] = round(st.mean(values), 4) if values else None

    long_short = {}
    for factor in FACTORS:
        summary = next((row for row in summaries if row["factor"] == factor), None)
        if summary is None:
            continue
        orientation = summary["orientation"]
        monthly = []
        for day in [d for d in month_ends if d >= VALIDATION_START]:
            members = _members_on(snapshot_dates, membership_map, day)
            ext_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
            if not ext_path.exists():
                continue
            ext = pl.read_parquet(ext_path).select(["symbol", "analyst_rating_score_20d"])
            factors = _month_factor_frame(day, members, reports, ext)
            fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
            joined = factors.join(fwd, on="symbol", how="inner").filter(pl.col(factor).is_not_null())
            if joined.height < 2 * MIN_LEG:
                continue
            joined = joined.with_columns((pl.col(factor) * orientation).alias("_oriented")).sort("_oriented", descending=True)
            n = joined.height
            cut = max(MIN_LEG, int(n * 0.30))
            top = joined.head(cut)["forward_return"].mean()
            bottom = joined.tail(cut)["forward_return"].mean()
            if top is not None and bottom is not None:
                monthly.append(float(top - bottom - COST))
        long_short[factor] = {
            "months": len(monthly),
            "mean_net": round(st.mean(monthly), 6) if monthly else None,
            "positive_rate": round(sum(v > 0 for v in monthly) / len(monthly), 4) if monthly else None,
            "p05": round(sorted(monthly)[max(0, int(len(monthly) * 0.05))], 6) if monthly else None,
            "worst": round(min(monthly), 6) if monthly else None,
        }

    d1 = (
        len(summaries) == len(FACTORS)
        and len({row["date"] for row in records if row["split"] == "discovery"}) >= 20
        and len({row["date"] for row in records if row["split"] == "validation"}) >= 24
        and all((row["validation_coverage"] or 0) >= 0.60 for row in summaries)
    )
    g1_candidates = [
        row for row in summaries
        if row["discovery_abs_ic"] >= 0.03
        and row["discovery_win_rate"] >= 0.55
        and row["discovery_coverage"] >= 0.60
    ]
    selected = [
        row for row in g1_candidates
        if row["validation_ic_oriented"] is not None
        and row["validation_ic_oriented"] >= 0.015
        and row["validation_win_rate"] is not None
        and row["validation_win_rate"] >= 0.52
        and row["validation_weak_ic"] is not None
        and row["validation_weak_ic"] >= 0.01
        and (rating_corr.get(row["factor"]) is None or rating_corr[row["factor"]] < 0.70)
        and long_short.get(row["factor"], {}).get("mean_net") is not None
        and long_short[row["factor"]]["mean_net"] > 0
    ]
    decision = {
        "D1": d1,
        "G1": len(g1_candidates) >= 1,
        "G2": any(row["validation_ic_oriented"] is not None and row["validation_ic_oriented"] >= 0.015 for row in g1_candidates),
        "G3": any(row["validation_weak_ic"] is not None and row["validation_weak_ic"] >= 0.01 for row in g1_candidates),
        "G4": any((rating_corr.get(row["factor"]) is None or rating_corr[row["factor"]] < 0.70) for row in g1_candidates),
        "G5": any(long_short.get(row["factor"], {}).get("mean_net") is not None and long_short[row["factor"]]["mean_net"] > 0 for row in g1_candidates),
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R167 analyst target-price factor scan",
        "data": "r47_analyst target_low/target_high; ext_data analyst_rating_score_20d",
        "window": [str(DISCOVERY_START), str(VALIDATION_END)],
        "split": {"discovery": [str(DISCOVERY_START), str(DISCOVERY_END)], "validation": [str(VALIDATION_START), str(VALIDATION_END)]},
        "method": {
            "forward_return": "T+1 open to T+21 open",
            "ic": "monthly cross-sectional Spearman rank IC",
            "coverage": "non-null factor members / historical HS300 members",
            "long_short": "oriented top 30% minus bottom 30%, monthly, 0.30% round-trip cost",
            "minimum_names": MIN_NAMES,
        },
        "summaries": summaries,
        "rating_score_correlation": rating_corr,
        "long_short_validation": long_short,
        "g1_candidates": g1_candidates,
        "selected": selected,
        "decision": decision,
        "records": records,
        "caveats": [
            "Target-price coverage is low in 2022 and rises over time; validation is 2024-2026.",
            "The long-short leg does not model limit-up/down or T+1 execution constraints beyond open-to-open returns.",
            "Only the latest report per symbol is used for upside; revision/dispersion use windows described in the ledger.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("summaries:", json.dumps(summaries, ensure_ascii=False), flush=True)
    print("rating corr:", json.dumps(rating_corr, ensure_ascii=False), flush=True)
    print("long short:", json.dumps(long_short, ensure_ascii=False), flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
