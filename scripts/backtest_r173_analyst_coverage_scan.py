#!/usr/bin/env python3
"""R173: analyst coverage breadth expansion factor scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r173_analyst_coverage_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
ANALYST_DIR = DATA / "research" / "r47_analyst"
DISCOVERY_START = date(2021, 7, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
MIN_NAMES = 60
MIN_LEG = 20
COST = 0.003
FACTOR = "coverage_change_90d"
CONTROLS = ["analyst_rating_score_20d", "analyst_rating_change_20d"]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _reports() -> pl.DataFrame:
    files = sorted(ANALYST_DIR.glob("*.parquet"))
    return (
        pl.scan_parquet([str(path) for path in files])
        .select(["symbol", "report_date", "institution"])
        .collect()
        .with_columns(pl.col("report_date").cast(pl.Date))
        .drop_nulls(["symbol", "report_date", "institution"])
        .unique()
        .sort(["symbol", "report_date"])
    )


def _rank_corr(frame: pl.DataFrame, left: str, right: str) -> float | None:
    part = frame.select([left, right]).drop_nulls()
    if part.height < MIN_NAMES or part[left].n_unique() < 2 or part[right].n_unique() < 2:
        return None
    x = part[left].rank("average")
    y = part[right].rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residualize(part: pl.DataFrame, factor: str, controls: list[str]) -> pl.DataFrame:
    out = part
    residual = factor
    for index, control in enumerate(controls):
        usable = out.select([residual, control]).drop_nulls()
        n = usable.height
        if n < 3 or usable[control].n_unique() < 2:
            continue
        xs = usable[control].to_list()
        ys = usable[residual].to_list()
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            continue
        beta = (n * sxy - sx * sy) / denom
        alpha = (sy - beta * sx) / n
        name = f"_resid_{index}"
        out = out.with_columns((pl.col(residual) - alpha - beta * pl.col(control)).alias(name))
        residual = name
    return out.with_columns(pl.col(residual).alias("_residual"))


def _long_short(joined: pl.DataFrame) -> dict | None:
    part = joined.select([FACTOR, "forward_return"]).drop_nulls()
    if part.height < 2 * MIN_LEG:
        return None
    part = part.sort(FACTOR, descending=True)
    cut = max(MIN_LEG, int(part.height * 0.30))
    top = part.head(cut)["forward_return"].mean()
    bottom = part.tail(cut)["forward_return"].mean()
    if top is None or bottom is None:
        return None
    return {"net": float(top - bottom - COST), "names": part.height}


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
    prices = r167._prices()
    forward = r167._forward_returns(calendar, month_ends, prices)
    snapshot_dates, membership_map = r167._membership()
    reports = _reports()

    records = []
    for day in month_ends:
        members = r167._members_on(snapshot_dates, membership_map, day)
        if not members:
            continue
        available = reports.filter(pl.col("report_date") <= day - timedelta(days=1))
        cur = (
            available.filter(pl.col("report_date") > day - timedelta(days=90))
            .filter(pl.col("symbol").is_in(members))
            .group_by("symbol")
            .agg(pl.col("institution").n_unique().alias("_cur"))
        )
        prev = (
            available.filter((pl.col("report_date") > day - timedelta(days=180)) & (pl.col("report_date") <= day - timedelta(days=90)))
            .filter(pl.col("symbol").is_in(members))
            .group_by("symbol")
            .agg(pl.col("institution").n_unique().alias("_prev"))
        )
        factors = (
            pl.DataFrame({"symbol": sorted(members)})
            .join(cur, on="symbol", how="left")
            .join(prev, on="symbol", how="left")
            .with_columns(pl.col("_cur").fill_null(0), pl.col("_prev").fill_null(0))
            .filter((pl.col("_cur") + pl.col("_prev")) > 0)
            .with_columns((pl.col("_cur") - pl.col("_prev")).alias(FACTOR))
        )
        ext_path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
        if ext_path.exists():
            ext = pl.read_parquet(ext_path).select(["symbol", *CONTROLS])
            factors = factors.join(ext, on="symbol", how="left")
        else:
            factors = factors.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in CONTROLS])
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        joined = factors.join(fwd, on="symbol", how="inner")
        ic = _rank_corr(joined, FACTOR, "forward_return")
        ls = _long_short(joined)
        residual = _residualize(joined, FACTOR, CONTROLS) if joined.height else joined
        residual_ic = _rank_corr(residual, "_residual", "forward_return") if residual.height else None
        corr_values = [_rank_corr(joined, FACTOR, control) for control in CONTROLS]
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        records.append({
            "date": str(day),
            "split": split,
            "members": len(members),
            "factor_names": factors.height,
            "coverage": round(factors.height / len(members), 4) if members else 0.0,
            "ic": round(ic, 6) if ic is not None else None,
            "residual_ic": round(residual_ic, 6) if residual_ic is not None else None,
            "control_corr_score": round(corr_values[0], 6) if corr_values[0] is not None else None,
            "control_corr_change": round(corr_values[1], 6) if corr_values[1] is not None else None,
            "long_short_net": round(ls["net"], 6) if ls is not None else None,
            "long_short_names": ls["names"] if ls is not None else None,
        })

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in records if r["split"] == split]
        ic_rows = [r for r in part if r["ic"] is not None]
        ls_values = [r["long_short_net"] for r in part if r["long_short_net"] is not None]
        ic_values = [float(r["ic"]) for r in ic_rows]
        summary[split] = {
            "months": len(part),
            "ic_months": len(ic_rows),
            "ic_mean": round(st.mean(ic_values), 6) if ic_values else None,
            "ic_win_rate": round(sum(v > 0 for v in ic_values) / len(ic_values), 4) if ic_values else None,
            "coverage_mean": round(st.mean(r["coverage"] for r in part), 4) if part else None,
            "min_factor_names": min((r["factor_names"] for r in part), default=None),
            "residual_ic_mean": round(st.mean(r["residual_ic"] for r in ic_rows if r["residual_ic"] is not None), 6) if any(r["residual_ic"] is not None for r in ic_rows) else None,
            "control_corr_score_mean": round(st.mean(r["control_corr_score"] for r in ic_rows if r["control_corr_score"] is not None), 4) if any(r["control_corr_score"] is not None for r in ic_rows) else None,
            "control_corr_change_mean": round(st.mean(r["control_corr_change"] for r in ic_rows if r["control_corr_change"] is not None), 4) if any(r["control_corr_change"] is not None for r in ic_rows) else None,
            "long_short": _stats(ls_values),
            "long_short_compound": round(_compound(ls_values), 6) if ls_values else None,
            "long_short_mdd": round(_mdd(ls_values), 6) if ls_values else None,
        }
    recent_ls = [r["long_short_net"] for r in records if r["split"] == "holdout" and r["long_short_net"] is not None][-12:]
    recent_12m = round(_compound(recent_ls), 6) if recent_ls else None

    d1 = all(
        summary[s]["months"] >= threshold
        and summary[s]["coverage_mean"] is not None
        and summary[s]["coverage_mean"] >= 0.60
        and summary[s]["min_factor_names"] is not None
        and summary[s]["min_factor_names"] >= MIN_NAMES
        for s, threshold in [("discovery", 18), ("validation", 24), ("holdout", 18)]
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
    max_corr = max(
        abs(summary["validation"]["control_corr_score_mean"] or 0.0),
        abs(summary["validation"]["control_corr_change_mean"] or 0.0),
    )
    g5 = max_corr < 0.70 and summary["validation"]["residual_ic_mean"] is not None and summary["validation"]["residual_ic_mean"] >= 0.010
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R173 analyst coverage breadth expansion",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "analyst_reports": reports.height,
            "symbols": reports["symbol"].n_unique(),
            "start": str(reports["report_date"].min()),
            "end": str(reports["report_date"].max()),
        },
        "method": {
            "factor": "distinct institutions reporting in last 90d minus prior 90d",
            "direction": "positive",
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
            "Coverage counts unique institution names as reported by the source; institution name changes are not normalized.",
            "Only covered stocks are eligible, so zero-coverage names are excluded.",
            "The report sample starts in 2021-06, limiting the discovery window to 18 months.",
            "Local price history covers about 275 old HS300 names; newer names have shorter history.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_short": recent_12m, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())