#!/usr/bin/env python3
"""R166: weak-regime factor scan with a discovery/validation split."""
from __future__ import annotations

import bisect
import json
import math
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r166_weak_regime_factor_scan.json"
EXT_CONFIG = DATA / "ext_data" / "alt_hs300" / "config.json"
MONTHLY_MEMBERSHIP = DATA / "research" / "r157_membership" / "monthly_snapshots.parquet"
DAILY_MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
MIN_NAMES = 30


def _membership() -> tuple[list[date], dict[date, set[str]]]:
    monthly = pl.read_parquet(MONTHLY_MEMBERSHIP).select([
        pl.col("query_date").cast(pl.Date).alias("snapshot_date"),
        pl.col("symbol").cast(pl.String),
    ])
    daily = pl.read_parquet(DAILY_MEMBERSHIP).select([
        pl.col("query_date").cast(pl.Date).alias("snapshot_date"),
        pl.col("symbol").cast(pl.String),
    ])
    frame = pl.concat([monthly, daily]).unique().sort(["snapshot_date", "symbol"])
    dates = sorted(frame["snapshot_date"].unique().to_list())
    mapping = {}
    for snapshot, part in frame.group_by("snapshot_date", maintain_order=True):
        mapping[snapshot[0]] = set(part["symbol"].to_list())
    return dates, mapping


def _snapshot_symbols(snapshot_dates: list[date], mapping: dict[date, set[str]], day: date) -> set[str]:
    pos = bisect.bisect_right(snapshot_dates, day) - 1
    if pos < 0:
        return set()
    return mapping[snapshot_dates[pos]]


def _month_ends() -> list[date]:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    dates = (
        pl.scan_parquet([str(path) for path in paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    frame = pl.DataFrame({"date": dates}).with_columns([
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    ])
    return frame.group_by(["year", "month"]).agg(pl.col("date").max()).sort(["year", "month"])["date"].to_list()


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def _forward_returns(calendar: list[date], rebalance_dates: list[date]) -> pl.DataFrame:
    index = {day: i for i, day in enumerate(calendar)}
    rows = []
    for day in rebalance_dates:
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
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    prices = (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date", "open"])
        .collect()
    )
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
    return None if value is None or not math.isfinite(float(value)) else float(value)


def _factor_correlation(panel: pl.DataFrame, factors: list[str]) -> dict:
    out = {}
    for i, left in enumerate(factors):
        for right in factors[i + 1 :]:
            values = []
            for (_,), part in panel.group_by("date", maintain_order=True):
                pair = part.select([left, right]).drop_nulls()
                if pair.height < MIN_NAMES or pair[left].n_unique() < 2 or pair[right].n_unique() < 2:
                    continue
                x = pair[left].rank("average")
                y = pair[right].rank("average")
                value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
                if value is not None and math.isfinite(float(value)):
                    values.append(float(value))
            if values:
                out[f"{left}|{right}"] = round(st.mean(values), 4)
    return out


def main() -> int:
    config = json.loads(EXT_CONFIG.read_text(encoding="utf-8"))
    factors = [field["name"] for field in config["fields"]]
    snapshot_dates, membership_map = _membership()
    rebalance_dates = _month_ends()
    rebalance_dates = [day for day in rebalance_dates if day >= date(2018, 1, 1)]
    calendar = (
        pl.scan_parquet([str(path) for path in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    forward = _forward_returns(calendar, rebalance_dates)
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_values = index["close"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}

    month_frames = {}
    month_regime = {}
    for day in rebalance_dates:
        pos = index_pos.get(day)
        if pos is None or pos < 60:
            continue
        regime = "weak" if index_values[pos] / index_values[pos - 60] - 1.0 <= 0 else "strong"
        symbols = _snapshot_symbols(snapshot_dates, membership_map, day)
        if not symbols:
            continue
        path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
        if not path.exists():
            continue
        ext = pl.read_parquet(path).filter(pl.col("symbol").is_in(symbols))
        month_frames[day] = ext
        month_regime[day] = regime

    records = []
    for day, ext in month_frames.items():
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        if fwd.height < MIN_NAMES:
            continue
        joined = ext.join(fwd, on="symbol", how="inner")
        if joined.height < MIN_NAMES:
            continue
        membership_count = len(_snapshot_symbols(snapshot_dates, membership_map, day))
        split = "discovery" if day <= DISCOVERY_END else "validation"
        for factor in factors:
            if factor not in joined.columns:
                continue
            non_null = joined[factor].is_not_null().sum()
            ic = _rank_ic(joined, factor)
            records.append({
                "date": day.isoformat(),
                "split": split,
                "regime": month_regime[day],
                "factor": factor,
                "ic": ic,
                "coverage": round(float(non_null) / membership_count, 6) if membership_count else 0.0,
            })

    summaries = []
    for factor in factors:
        part = [row for row in records if row["factor"] == factor]
        disc_weak = [row for row in part if row["split"] == "discovery" and row["regime"] == "weak" and row["ic"] is not None]
        val_weak = [row for row in part if row["split"] == "validation" and row["regime"] == "weak" and row["ic"] is not None]
        val_strong = [row for row in part if row["split"] == "validation" and row["regime"] == "strong" and row["ic"] is not None]
        if not disc_weak:
            continue
        disc_ic = st.mean(row["ic"] for row in disc_weak)
        orientation = 1.0 if disc_ic >= 0 else -1.0
        val_oriented = st.mean(orientation * row["ic"] for row in val_weak) if val_weak else None
        strong_oriented = st.mean(orientation * row["ic"] for row in val_strong) if val_strong else None
        summaries.append({
            "factor": factor,
            "orientation": orientation,
            "discovery_weak_ic": round(disc_ic, 6),
            "discovery_weak_abs_ic": round(abs(disc_ic), 6),
            "discovery_weak_win_rate": round(sum(row["ic"] * orientation > 0 for row in disc_weak) / len(disc_weak), 4),
            "discovery_weak_coverage": round(st.mean(row["coverage"] for row in disc_weak), 4),
            "validation_weak_ic_oriented": round(val_oriented, 6) if val_oriented is not None else None,
            "validation_weak_win_rate": round(sum(row["ic"] * orientation > 0 for row in val_weak) / len(val_weak), 4) if val_weak else None,
            "validation_strong_ic_oriented": round(strong_oriented, 6) if strong_oriented is not None else None,
            "discovery_weak_months": len(disc_weak),
            "validation_weak_months": len(val_weak),
            "validation_strong_months": len(val_strong),
        })

    discovery_weak_months = len({row["date"] for row in records if row["split"] == "discovery" and row["regime"] == "weak"})
    validation_weak_months = len({row["date"] for row in records if row["split"] == "validation" and row["regime"] == "weak"})
    g1_candidates = [
        row for row in summaries
        if row["discovery_weak_abs_ic"] >= 0.03
        and row["discovery_weak_coverage"] >= 0.70
        and row["discovery_weak_win_rate"] >= 0.55
    ]
    selected = [
        row for row in g1_candidates
        if row["validation_weak_ic_oriented"] is not None
        and row["validation_weak_ic_oriented"] >= 0.01
        and row["validation_weak_win_rate"] is not None
        and row["validation_weak_win_rate"] >= 0.50
        and row["validation_strong_ic_oriented"] is not None
        and row["validation_strong_ic_oriented"] >= -0.01
    ]

    panel_frames = []
    for day in month_frames:
        ext = month_frames[day]
        cols = [factor for factor in factors if factor in ext.columns]
        panel_frames.append(ext.select(["symbol", *cols]).with_columns(pl.lit(day).alias("date")))
    panel = pl.concat(panel_frames, how="diagonal_relaxed") if panel_frames else pl.DataFrame()
    selected_names = [row["factor"] for row in selected]
    correlations = _factor_correlation(panel, selected_names) if len(selected_names) >= 2 else {}
    diverse_triplet = False
    for i in range(len(selected_names)):
        for j in range(i + 1, len(selected_names)):
            for k in range(j + 1, len(selected_names)):
                keys = [f"{selected_names[a]}|{selected_names[b]}" for a, b in ((i, j), (i, k), (j, k))]
                if all(correlations.get(key, 1.0) < 0.80 for key in keys):
                    diverse_triplet = True
                    break
            if diverse_triplet:
                break
        if diverse_triplet:
            break

    d1 = len(factors) >= 50 and discovery_weak_months >= 24 and validation_weak_months >= 12
    g1 = len(g1_candidates) >= 3
    g2 = len(selected) >= 3
    g3 = g2 and all(row["validation_strong_ic_oriented"] >= -0.01 for row in selected)
    g4 = diverse_triplet
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R166 weak-regime factor scan",
        "window": ["2018-01-01", "2026-10-16"],
        "regime": "HS300 60-day return <= 0 at month-end",
        "split": {"discovery": ["2018-01-01", "2023-12-31"], "validation": ["2024-01-01", "2026-09-30"]},
        "method": {
            "factor_values": "ext_data at month-end T",
            "forward_return": "T+1 open to T+21 open",
            "ic": "cross-sectional Spearman rank IC within historical HS300 members",
            "minimum_names": MIN_NAMES,
        },
        "coverage": {
            "factors": len(factors),
            "months": len(month_frames),
            "discovery_weak_months": discovery_weak_months,
            "validation_weak_months": validation_weak_months,
        },
        "g1_candidates": g1_candidates,
        "selected": selected,
        "selected_correlations": correlations,
        "decision": decision,
        "summaries": sorted(summaries, key=lambda row: row["discovery_weak_abs_ic"], reverse=True),
        "records": records,
        "caveats": [
            "Discovery and validation are time-separated, but the factor list was already used in prior research.",
            "IC measures rank association, not tradability after costs and limit-up constraints.",
            "Macro/global fields have no cross-sectional variation and will be filtered by rank IC.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("coverage:", result["coverage"], flush=True)
    print("G1 candidates:", json.dumps(g1_candidates, ensure_ascii=False), flush=True)
    print("selected:", json.dumps(selected, ensure_ascii=False), flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
