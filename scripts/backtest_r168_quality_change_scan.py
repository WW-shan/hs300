#!/usr/bin/env python3
"""R168: quality-change / acceleration factor scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r168_quality_change_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2022, 5, 1)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
VALIDATION_END = date(2026, 9, 30)
MIN_NAMES = 30
MIN_LEG = 10
COST = 0.003
FACTORS = ["ocf_ni_chg_yoy", "accrual_chg_yoy_low", "roe_chg_yoy", "net_margin_chg_yoy"]
LEVELS = {
    "ocf_ni_chg_yoy": "ocf_ni",
    "accrual_chg_yoy_low": "accrual",
    "roe_chg_yoy": "roe",
    "net_margin_chg_yoy": "net_margin",
}
EFFECTIVE = {
    "ocf_ni_chg_yoy": "_announce_ocf_ni",
    "accrual_chg_yoy_low": "_announce_accrual",
    "roe_chg_yoy": "_announce_metrics",
    "net_margin_chg_yoy": "_announce_metrics",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _build_snapshot() -> pl.DataFrame:
    from app.backtest.fundamentals import load_fundamental_snapshot

    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None or snapshot.is_empty():
        raise RuntimeError("fundamental snapshot is empty")
    period_index = pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()
    prev_index = period_index.shift(4).over("symbol")
    consecutive = (period_index - prev_index) == 4
    ttm_ok = pl.col("_ttm_complete") & pl.col("_ttm_complete").shift(4).over("symbol")
    return snapshot.with_columns([
        pl.when(consecutive & ttm_ok)
        .then(pl.col("ocf_ni") - pl.col("ocf_ni").shift(4).over("symbol"))
        .otherwise(None)
        .alias("ocf_ni_chg_yoy"),
        pl.when(consecutive & ttm_ok)
        .then(-(pl.col("accrual") - pl.col("accrual").shift(4).over("symbol")))
        .otherwise(None)
        .alias("accrual_chg_yoy_low"),
        pl.when(consecutive)
        .then(pl.col("roe") - pl.col("roe").shift(4).over("symbol"))
        .otherwise(None)
        .alias("roe_chg_yoy"),
        pl.when(consecutive)
        .then(pl.col("net_margin") - pl.col("net_margin").shift(4).over("symbol"))
        .otherwise(None)
        .alias("net_margin_chg_yoy"),
    ])


def _month_factor_frame(day: date, members: set[str], snapshot: pl.DataFrame) -> pl.DataFrame:
    out = pl.DataFrame({"symbol": sorted(members), "date": [day] * len(members)})
    for factor in FACTORS:
        effective = EFFECTIVE[factor]
        level = LEVELS[factor]
        right = (
            snapshot.select(["symbol", effective, factor, level])
            .filter(pl.col(effective).is_not_null())
            .with_columns(pl.col(effective).dt.offset_by("1d").alias("_effective"))
            .sort(["symbol", "_effective"])
        )
        out = (
            out.sort(["symbol", "date"])
            .join_asof(
                right,
                left_on="date",
                right_on="_effective",
                by="symbol",
                strategy="backward",
                check_sortedness=False,
            )
            .with_columns(pl.col(level).alias(f"_level_{factor}"))
            .drop(effective, level, "_effective")
        )
    return out


def _corr(part: pl.DataFrame, left: str, right: str) -> float | None:
    pair = part.select([left, right]).drop_nulls()
    if pair.height < MIN_NAMES or pair[left].n_unique() < 2 or pair[right].n_unique() < 2:
        return None
    x = pair[left].rank("average")
    y = pair[right].rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= VALIDATION_END]
    prices = r167._prices()
    snapshot = _build_snapshot()
    snapshot_dates, membership_map = r167._membership()
    forward = r167._forward_returns(calendar, month_ends, prices)
    index = r167._index_daily()
    index_values = index["close"].to_list()
    index_pos = {day: i for i, day in enumerate(index["date"].to_list())}

    records = []
    level_corrs = {factor: [] for factor in FACTORS}
    for day in month_ends:
        members = r167._members_on(snapshot_dates, membership_map, day)
        if len(members) < MIN_NAMES:
            continue
        factors = _month_factor_frame(day, members, snapshot)
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        joined = factors.join(fwd, on="symbol", how="inner")
        if joined.height < MIN_NAMES:
            continue
        regime = r167._regime(day, index_values, index_pos)
        split = "discovery" if day <= DISCOVERY_END else "validation"
        for factor in FACTORS:
            non_null = joined[factor].is_not_null().sum()
            ic = r167._rank_ic(joined, factor)
            records.append({
                "date": day.isoformat(),
                "split": split,
                "regime": regime,
                "factor": factor,
                "ic": ic,
                "coverage": round(float(non_null) / len(members), 6),
                "n": int(non_null),
            })
            corr = _corr(joined, factor, f"_level_{factor}")
            if corr is not None:
                level_corrs[factor].append(abs(corr))

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
            "level_correlation": round(st.mean(level_corrs[factor]), 4) if level_corrs[factor] else None,
        })

    long_short = {}
    for factor in FACTORS:
        summary = next((row for row in summaries if row["factor"] == factor), None)
        if summary is None:
            continue
        orientation = summary["orientation"]
        monthly = []
        for day in [d for d in month_ends if d >= VALIDATION_START]:
            members = r167._members_on(snapshot_dates, membership_map, day)
            factors = _month_factor_frame(day, members, snapshot)
            fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
            joined = factors.join(fwd, on="symbol", how="inner").filter(pl.col(factor).is_not_null())
            if joined.height < 2 * MIN_LEG:
                continue
            joined = joined.with_columns((pl.col(factor) * orientation).alias("_oriented")).sort("_oriented", descending=True)
            cut = max(MIN_LEG, int(joined.height * 0.30))
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
        and len({row["date"] for row in records if row["split"] == "discovery"}) >= 18
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
        and (row["level_correlation"] is None or row["level_correlation"] < 0.70)
        and long_short.get(row["factor"], {}).get("mean_net") is not None
        and long_short[row["factor"]]["mean_net"] > 0
    ]
    decision = {
        "D1": d1,
        "G1": len(g1_candidates) >= 1,
        "G2": any(row["validation_ic_oriented"] is not None and row["validation_ic_oriented"] >= 0.015 for row in g1_candidates),
        "G3": any(row["validation_weak_ic"] is not None and row["validation_weak_ic"] >= 0.01 for row in g1_candidates),
        "G4": any(long_short.get(row["factor"], {}).get("mean_net") is not None and long_short[row["factor"]]["mean_net"] > 0 for row in g1_candidates),
        "G5": any((row["level_correlation"] is None or row["level_correlation"] < 0.70) for row in g1_candidates),
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R168 quality-change / acceleration factor scan",
        "data": "data/financials; PIT via backend.app.backtest.fundamentals",
        "window": [str(DISCOVERY_START), str(VALIDATION_END)],
        "split": {"discovery": [str(DISCOVERY_START), str(DISCOVERY_END)], "validation": [str(VALIDATION_START), str(VALIDATION_END)]},
        "method": {
            "change": "current minus same quarter last year",
            "forward_return": "T+1 open to T+21 open",
            "ic": "monthly cross-sectional Spearman rank IC",
            "long_short": "oriented top 30% minus bottom 30%, monthly, 0.30% round-trip cost",
            "minimum_names": MIN_NAMES,
        },
        "summaries": summaries,
        "long_short_validation": long_short,
        "g1_candidates": g1_candidates,
        "selected": selected,
        "decision": decision,
        "records": records,
        "caveats": [
            "Financial data starts 2021Q1; discovery starts 2022-05 after four-quarter TTM warmup.",
            "The long-short leg does not model limit-up/down or T+1 execution constraints beyond open-to-open returns.",
            "Change factors are compared with their corresponding level factor to reject level proxies.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("summaries:", json.dumps(summaries, ensure_ascii=False), flush=True)
    print("long short:", json.dumps(long_short, ensure_ascii=False), flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
