#!/usr/bin/env python3
"""R181: short-interest crowding factor scan (short_qty based)."""
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
OUT = EVIDENCE / "r181_short_interest_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
MARGIN_DIR = DATA / "research" / "r50_margin"
DISCOVERY_START = date(2021, 6, 1)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
VALIDATION_END = date(2026, 9, 30)
MIN_NAMES = 60
MIN_LEG = 20
COST = 0.003
FACTORS = ["short_ratio", "short_chg_1m"]
CONTROLS = ["margin_chg_1m", "momentum_20d", "annual_vol_20d", "float_mv"]


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
    if not files:
        raise RuntimeError(f"no margin snapshots in {MARGIN_DIR}")
    inst = (
        pl.read_parquet(DATA / "instruments" / "instruments.parquet")
        .select(["symbol", "float_shares"])
        .unique(subset=["symbol"])
    )
    frame = (
        pl.concat([pl.read_parquet(f) for f in files])
        .select(["symbol", "snapshot_date", "margin_balance", "short_qty"])
        .unique()
        .sort(["symbol", "snapshot_date"])
    )
    frame = frame.with_columns([
        pl.col("short_qty").shift(1).over("symbol").alias("_prev_short"),
        pl.col("margin_balance").shift(1).over("symbol").alias("_prev_balance"),
        pl.col("snapshot_date").shift(1).over("symbol").alias("_prev_date"),
    ]).with_columns(
        (pl.col("snapshot_date") - pl.col("_prev_date")).dt.total_days().alias("_gap_days")
    ).with_columns([
        pl.when(
            pl.col("_gap_days").is_between(20, 45, closed="both")
            & (pl.col("_prev_short") > 0) & (pl.col("short_qty") >= 0)
        ).then(pl.col("short_qty") / pl.col("_prev_short") - 1.0).otherwise(None).alias("short_chg_1m"),
        pl.when(
            pl.col("_gap_days").is_between(20, 45, closed="both")
            & (pl.col("_prev_balance") > 0) & (pl.col("margin_balance") > 0)
        ).then(pl.col("margin_balance") / pl.col("_prev_balance") - 1.0).otherwise(None).alias("margin_chg_1m"),
        pl.col("snapshot_date").dt.offset_by("1d").alias("available_date"),
    ]).drop(["_prev_short", "_prev_balance", "_prev_date", "_gap_days"])
    frame = frame.join(inst, on="symbol", how="left")
    return frame.with_columns([
        pl.when((pl.col("float_shares") > 0) & (pl.col("short_qty") >= 0))
        .then(pl.col("short_qty") / pl.col("float_shares")).otherwise(None).alias("short_ratio"),
    ])


def _controls_frame() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    prices = (
        pl.scan_parquet([str(path) for path in paths])
        .select(["symbol", "date", "close", "raw_close"])
        .collect()
        .sort(["symbol", "date"])
    )
    inst = (
        pl.read_parquet(DATA / "instruments" / "instruments.parquet")
        .select(["symbol", "float_shares"])
        .unique(subset=["symbol"])
    )
    frame = prices.with_columns(
        pl.col("close").shift(1).over("symbol").alias("_prev_close")
    ).with_columns(
        (pl.col("close") / pl.col("close").shift(20).over("symbol") - 1.0).alias("momentum_20d"),
        ((pl.col("close") / pl.col("_prev_close")).log().rolling_std(window_size=20, min_samples=20).over("symbol") * math.sqrt(252.0)).alias("annual_vol_20d"),
    ).join(inst, on="symbol", how="left").with_columns(
        (pl.col("raw_close") * pl.col("float_shares")).alias("float_mv")
    )
    return frame.select(["symbol", "date", "momentum_20d", "annual_vol_20d", "float_mv"])


def _attach(reb: pl.DataFrame, snaps: pl.DataFrame) -> pl.DataFrame:
    joined = reb.sort(["symbol", "date"]).join_asof(
        snaps.select(["symbol", "available_date", *FACTORS, "margin_chg_1m"]).sort(["symbol", "available_date"]),
        left_on="date", right_on="available_date", by="symbol", strategy="backward", check_sortedness=False,
    ).with_columns((pl.col("date") - pl.col("available_date")).dt.total_days().alias("age_days"))
    joined = joined.with_columns([
        pl.when(pl.col("age_days").is_between(0, 45, closed="both"))
        .then(pl.col(name)).otherwise(None).alias(name)
        for name in [*FACTORS, "margin_chg_1m"]
    ])
    for name in FACTORS:
        joined = joined.with_columns([
            pl.col(name).quantile(0.01).over("date").alias("_lo"),
            pl.col(name).quantile(0.99).over("date").alias("_hi"),
        ]).with_columns(pl.col(name).clip(pl.col("_lo"), pl.col("_hi")).alias(name)).drop(["_lo", "_hi"])
    return joined


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


def _residual_ic(frame: pl.DataFrame, factor: str, control: str) -> float | None:
    part = frame.select([factor, control, "forward_return"]).drop_nulls()
    n = part.height
    if n < MIN_NAMES or part[factor].n_unique() < 2 or part[control].n_unique() < 2:
        return None
    xs = part[control].to_list()
    ys = part[factor].to_list()
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    residual = part.with_columns((pl.col(factor) - alpha - beta * pl.col(control)).alias("_resid"))
    return _rank_corr(residual, "_resid", "forward_return")


def _long_short(joined: pl.DataFrame, factor: str) -> tuple[float, int] | None:
    part = joined.select([factor, "forward_return"]).drop_nulls()
    if part.height < 2 * MIN_LEG:
        return None
    part = part.sort(factor, descending=True)
    cut = max(MIN_LEG, int(part.height * 0.30))
    top = part.head(cut)["forward_return"].mean()
    bottom = part.tail(cut)["forward_return"].mean()
    if top is None or bottom is None:
        return None
    return float(top - bottom - COST), part.height


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


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= VALIDATION_END]
    prices = r167._prices()
    forward = r167._forward_returns(calendar, month_ends, prices)
    daily_dates, daily_map = r167._membership()
    monthly_dates, monthly_map = _monthly_membership()
    snaps = _snapshots()
    controls_frame = _controls_frame()

    records = []
    for day in month_ends:
        if daily_dates and day >= daily_dates[0]:
            members = r167._members_on(daily_dates, daily_map, day)
        else:
            members = r167._members_on(monthly_dates, monthly_map, day)
        if not members:
            continue
        fwd = forward.filter(pl.col("rebalance_date") == day).select(["symbol", "forward_return"])
        if fwd.is_empty():
            continue
        base = pl.DataFrame({"symbol": sorted(members), "date": [day] * len(members)})
        joined = base.join(fwd, on="symbol", how="inner")
        joined = joined.join(controls_frame.filter(pl.col("date") == day).drop("date"), on="symbol", how="left")
        joined = _attach(joined, snaps)
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "other"
        )
        row = {"date": str(day), "split": split, "members": len(members)}
        for factor in FACTORS:
            signed = joined.with_columns((-pl.col(factor)).alias("_signed"))
            factor_names = int(joined[factor].is_not_null().sum())
            ic = _rank_corr(signed, "_signed", "forward_return")
            ls = _long_short(signed, "_signed")
            row[f"{factor}_names"] = factor_names
            row[f"{factor}_coverage"] = round(factor_names / len(members), 4) if members else 0.0
            row[f"{factor}_ic"] = round(ic, 6) if ic is not None else None
            row[f"{factor}_ls_net"] = round(ls[0], 6) if ls is not None else None
        row["margin_chg_ic"] = round(_rank_corr(joined, "margin_chg_1m", "forward_return"), 6) if _rank_corr(joined, "margin_chg_1m", "forward_return") is not None else None
        signed_joined = joined.with_columns((-pl.col("short_ratio")).alias("_short_ratio_low"))
        resid = _residual_ic(signed_joined, "_short_ratio_low", "margin_chg_1m")
        row["residual_ic_short_ratio"] = round(resid, 6) if resid is not None else None
        corr_values = {}
        for control in CONTROLS:
            value = _rank_corr(joined, "short_ratio", control)
            corr_values[control] = round(value, 6) if value is not None else None
        row["corr"] = corr_values
        records.append(row)

    summary = {}
    for split in ["discovery", "validation"]:
        part = [r for r in records if r["split"] == split]
        entry = {"months": len(part)}
        for factor in FACTORS:
            ic_values = [float(r[f"{factor}_ic"]) for r in part if r[f"{factor}_ic"] is not None]
            ls_values = [r[f"{factor}_ls_net"] for r in part if r[f"{factor}_ls_net"] is not None]
            entry[factor] = {
                "ic_months": len(ic_values),
                "ic_mean": round(st.mean(ic_values), 6) if ic_values else None,
                "ic_win_rate": round(sum(v > 0 for v in ic_values) / len(ic_values), 4) if ic_values else None,
                "coverage_mean": round(st.mean(r[f"{factor}_coverage"] for r in part), 4) if part else None,
                "min_names": min((r[f"{factor}_names"] for r in part), default=None),
                "long_short": _stats(ls_values),
                "long_short_compound": round(_compound(ls_values), 6) if ls_values else None,
            }
        corr_rows = [r["corr"] for r in part]
        entry["corr_max"] = {}
        for control in CONTROLS:
            values = [c[control] for c in corr_rows if c.get(control) is not None]
            entry["corr_max"][control] = round(st.mean(values), 4) if values else None
        residual_values = [r["residual_ic_short_ratio"] for r in part if r["residual_ic_short_ratio"] is not None]
        entry["residual_ic_short_ratio_mean"] = round(st.mean(residual_values), 6) if residual_values else None
        summary[split] = entry

    recent_ls = [r["short_ratio_ls_net"] for r in records if r["split"] == "validation" and r["short_ratio_ls_net"] is not None][-12:]
    recent_12m = round(_compound(recent_ls), 6) if recent_ls else None

    results = {}
    for factor in FACTORS:
        disc = summary["discovery"][factor]
        val = summary["validation"][factor]
        d1 = bool(
            disc["ic_months"] >= 24 and val["ic_months"] >= 24
            and disc["min_names"] is not None and disc["min_names"] >= MIN_NAMES
            and val["min_names"] is not None and val["min_names"] >= MIN_NAMES
            and disc["coverage_mean"] is not None and disc["coverage_mean"] >= 0.60
            and val["coverage_mean"] is not None and val["coverage_mean"] >= 0.60
        )
        g1 = disc["ic_mean"] is not None and disc["ic_mean"] >= 0.030 and disc["ic_win_rate"] >= 0.55
        g2 = val["ic_mean"] is not None and val["ic_mean"] >= 0.015 and val["ic_win_rate"] >= 0.52
        g3 = recent_12m is not None and recent_12m > 0
        g4 = (
            val["long_short"]["mean"] is not None and val["long_short"]["mean"] > 0
            and val["long_short"]["positive_rate"] >= 0.52
        )
        corr_ok = all(
            v is not None and abs(v) < 0.70
            for v in summary["validation"]["corr_max"].values()
        )
        g5 = corr_ok and summary["validation"]["residual_ic_short_ratio_mean"] is not None and summary["validation"]["residual_ic_short_ratio_mean"] >= 0.010
        g6 = (
            disc["ic_mean"] is not None and val["ic_mean"] is not None
            and disc["ic_mean"] * val["ic_mean"] > 0
        )
        decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
        decision["all_pass"] = all(decision.values())
        results[factor] = decision

    result = {
        "purpose": "R181 short-interest crowding (short_qty) factor scan",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "snapshots": "data/research/r50_margin (66 monthly snapshots, 2021-04-30..2026-09-30)",
            "snapshot_rows": snaps.height,
            "snapshot_symbols": snaps["symbol"].n_unique(),
            "short_qty_nonnull": int(snaps["short_qty"].is_not_null().sum()),
            "short_ratio_nonnull": int(snaps["short_ratio"].is_not_null().sum()),
            "short_chg_nonnull": int(snaps["short_chg_1m"].is_not_null().sum()),
            "membership": "monthly r157 before 2020-12-31; daily r78 thereafter",
        },
        "method": {
            "factors": {
                "short_ratio": "short_qty / float_shares (current instruments), direction low",
                "short_chg_1m": "short_qty / prev snapshot short_qty - 1, gap 20-45 days, direction low",
            },
            "pit": "available_date = snapshot_date + 1 day; age 0-45 days",
            "forward_return": "T+1 open to T+21 open",
            "ic": "monthly cross-sectional Spearman rank IC on negated factor (low is good)",
            "long_short": "top 30% minus bottom 30% on negated factor, monthly, 0.30% round-trip cost",
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_short": recent_12m,
        "decision": results,
        "monthly_records": records,
        "limitations": [
            "float_shares uses the current instruments snapshot, not PIT historical share counts.",
            "Margin snapshots only cover 2021-04 onwards; no pre-2021 test.",
            "Monthly 20-day forward returns overlap slightly at boundaries.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_short": recent_12m, "decision": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
