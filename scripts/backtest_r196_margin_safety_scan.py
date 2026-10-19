#!/usr/bin/env python3
"""R196: margin maintenance-ratio risk-avoidance timing scan for HS300."""
from __future__ import annotations

import bisect
import importlib.util
import json
import math
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r196_margin_safety_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
CACHE = DATA / "research" / "r178_margin_account" / "margin_account.parquet"
DISCOVERY_START = date(2018, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTORS = ["margin_safety_z", "margin_safety_chg_20d"]
CONTROLS = ["qvix_z_252", "equity_risk_premium"]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _margin_monthly() -> pl.DataFrame:
    frame = pl.read_parquet(CACHE).sort("date").filter(pl.col("maintenance_ratio").is_not_null())
    frame = frame.with_columns(
        (pl.col("maintenance_ratio") - pl.col("maintenance_ratio").shift(20)).alias("margin_safety_chg_20d")
    )
    frame = frame.with_columns([
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    ])
    month_last = (
        frame.group_by(["year", "month"])
        .agg([
            pl.col("date").max().alias("date"),
            pl.col("maintenance_ratio").last().alias("maintenance_ratio"),
            pl.col("margin_safety_chg_20d").last().alias("margin_safety_chg_20d"),
        ])
        .sort(["year", "month"])
    )
    month_last = month_last.with_columns(pl.int_range(1, pl.len() + 1).alias("_n")).with_columns([
        pl.when(pl.col("_n") >= 12).then(pl.col("maintenance_ratio").cum_sum() / pl.col("_n")).otherwise(None).alias("_mean"),
    ]).with_columns(
        pl.when(pl.col("_n") >= 12).then(
            ((pl.col("maintenance_ratio") ** 2).cum_sum() / pl.col("_n") - pl.col("_mean") ** 2).clip(lower_bound=0.0).sqrt()
        ).otherwise(None).alias("_std")
    ).with_columns(
        pl.when((pl.col("_n") >= 12) & (pl.col("_std") > 0))
        .then((pl.col("maintenance_ratio") - pl.col("_mean")) / pl.col("_std")).otherwise(None).alias("margin_safety_z")
    )
    return month_last.select(["date", "maintenance_ratio", "margin_safety_z", "margin_safety_chg_20d"])


def _index_forward(calendar: list[date], month_ends: list[date]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open"])
        .collect()
        .sort("date")
    )
    index_dates = index["date"].to_list()
    pos = {day: i for i, day in enumerate(index_dates)}
    rows = []
    for day in month_ends:
        i = pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            continue
        entry_day = index_dates[i + 1]
        exit_day = index_dates[i + 21]
        entry = index.filter(pl.col("date") == entry_day)["open"].item()
        exit_ = index.filter(pl.col("date") == exit_day)["open"].item()
        if entry and exit_:
            rows.append({"rebalance_date": day, "forward_return": float(exit_ / entry - 1.0)})
    return pl.DataFrame(rows)


def _control_for_day(day: date, column: str) -> float | None:
    path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return None
    values = pl.read_parquet(path).select(column).drop_nulls()
    return float(values[column][0]) if values.height else None


def _rank_corr(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) < 3 or len(values_x) != len(values_y):
        return None
    x = pl.Series(values_x).rank("average")
    y = pl.Series(values_y).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(rows: list[dict], factor: str) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[factor] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    ys = [r[factor] for r in usable]  # positive direction
    controls = [c for c in CONTROLS if all(r[c] is not None for r in usable)]
    corr_values = {}
    residual = ys
    for control in controls:
        xs = [r[control] for r in usable]
        n = len(usable)
        sx, sy = sum(xs), sum(residual)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, residual))
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            continue
        beta = (n * sxy - sx * sy) / denom
        alpha = (sy - beta * sx) / n
        residual = [y - alpha - beta * x for x, y in zip(xs, residual)]
        corr_values[control] = _rank_corr([r[factor] for r in usable], xs)
    forward = [r["forward_return"] for r in usable]
    resid_ic = _rank_corr(residual, forward)
    max_corr = max((abs(v) for v in corr_values.values() if v is not None), default=None)
    return max_corr, resid_ic


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


def _long_cash(rows: list[dict], factor: str) -> dict:
    position = 0
    strategy_returns = []
    benchmark_returns = []
    switches = 0
    for row in rows:
        signal = row[factor]
        if signal is None or row["forward_return"] is None:
            continue
        target = 1 if signal > 0 else 0
        cost = COST if target != position else 0.0
        switches += int(target != position)
        position = target
        strategy_returns.append(target * row["forward_return"] - cost)
        benchmark_returns.append(row["forward_return"])
    return {
        "months": len(strategy_returns),
        "strategy_total": round(_compound(strategy_returns), 6) if strategy_returns else None,
        "benchmark_total": round(_compound(benchmark_returns), 6) if benchmark_returns else None,
        "strategy_mdd": round(_mdd(strategy_returns), 6) if strategy_returns else None,
        "benchmark_mdd": round(_mdd(benchmark_returns), 6) if benchmark_returns else None,
        "switches": switches,
    }


def _ic_hit(rows: list[dict], factor: str) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[factor] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    ic = _rank_corr([r[factor] for r in usable], [r["forward_return"] for r in usable])
    hit = sum(1 for r in usable if (r[factor] > 0) == (r["forward_return"] > 0)) / len(usable)
    return ic, round(hit, 4)


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    margin = _margin_monthly()
    forward = _index_forward(calendar, month_ends)

    margin_map = {row["date"]: row for row in margin.iter_rows(named=True)}
    margin_dates = margin["date"].to_list()

    rows = []
    for row in forward.iter_rows(named=True):
        day = row["rebalance_date"]
        pos = bisect.bisect_right(margin_dates, day)
        if pos == 0:
            continue
        rec = margin_map[margin_dates[pos - 1]]
        record = {
            "date": str(day),
            "forward_return": row["forward_return"],
            "maintenance_ratio": rec["maintenance_ratio"],
            "margin_safety_z": rec["margin_safety_z"],
            "margin_safety_chg_20d": rec["margin_safety_chg_20d"],
        }
        for control in CONTROLS:
            record[control] = _control_for_day(day, control)
        rows.append(record)

    def split_of(day: date) -> str:
        if DISCOVERY_START <= day <= DISCOVERY_END:
            return "discovery"
        if VALIDATION_START <= day <= VALIDATION_END:
            return "validation"
        if HOLDOUT_START <= day <= HOLDOUT_END:
            return "holdout"
        return "other"

    for row in rows:
        row["split"] = split_of(date.fromisoformat(row["date"]))

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split]
        entry = {"months": len(part)}
        for factor in FACTORS:
            ic, hit = _ic_hit(part, factor)
            corr, resid = _residual_ic(part, factor)
            entry[factor] = {
                "ic": round(ic, 6) if ic is not None else None,
                "hit_rate": hit,
                "long_cash": _long_cash(part, factor),
                "max_control_corr": round(corr, 6) if corr is not None else None,
                "residual_ic": round(resid, 6) if resid is not None else None,
            }
        summary[split] = entry

    holdout_rows = [r for r in rows if r["split"] == "holdout"]
    recent_rows = holdout_rows[-12:]
    recent_lc = {f: _long_cash(recent_rows, f) for f in FACTORS}

    total_months = len(rows)
    results = {}
    for factor in FACTORS:
        d = summary["discovery"][factor]
        v = summary["validation"][factor]
        h = summary["holdout"][factor]
        signal_months = sum(1 for r in rows if r[factor] is not None)
        coverage = signal_months / total_months if total_months else 0.0
        d1 = total_months >= 90 and coverage >= 0.90
        g1 = d["ic"] is not None and d["ic"] >= 0.15 and d["hit_rate"] >= 0.55
        g2 = v["ic"] is not None and v["ic"] >= 0.10 and v["hit_rate"] >= 0.52
        g3 = (
            h["ic"] is not None and h["ic"] >= 0.05 and h["hit_rate"] >= 0.52
            and recent_lc[factor]["strategy_total"] is not None and recent_lc[factor]["strategy_total"] > 0
        )
        g4 = all(
            summary[s][factor]["long_cash"]["strategy_total"] is not None
            and summary[s][factor]["long_cash"]["benchmark_total"] is not None
            and summary[s][factor]["long_cash"]["strategy_total"] >= summary[s][factor]["long_cash"]["benchmark_total"] - 0.02
            and summary[s][factor]["long_cash"]["strategy_mdd"] is not None
            and summary[s][factor]["long_cash"]["strategy_mdd"] >= summary[s][factor]["long_cash"]["benchmark_mdd"] - 0.02
            for s in ["validation", "holdout"]
        )
        g5 = (
            v["max_control_corr"] is not None and v["max_control_corr"] < 0.70
            and v["residual_ic"] is not None and v["residual_ic"] >= 0.05
        )
        decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
        decision["all_pass"] = all(decision.values())
        results[factor] = decision

    result = {
        "purpose": "R196 margin maintenance-ratio risk-avoidance timing scan",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "source": "stock_margin_account_info via AkShare (R178 cache)",
            "months": total_months,
            "start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
        },
        "method": {
            "factors": {
                "margin_safety_z": "expanding z-score of maintenance ratio, direction positive",
                "margin_safety_chg_20d": "20d change of maintenance ratio, direction positive",
            },
            "long_cash": "signal > 0 holds HS300 index, else cash; 0.05% per switch",
            "forward_return": "T+1 open to T+21 open on 000300.SH",
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_cash": recent_lc,
        "decision": results,
        "monthly_records": rows,
        "limitations": [
            "Maintenance ratio is a market-level aggregate published by brokers; it may be mechanically correlated with contemporaneous market moves.",
            "Only 20d change and expanding z-score are tested; no threshold or window search.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "data": result["data"], "summary": summary, "recent_12m": recent_lc, "decision": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
