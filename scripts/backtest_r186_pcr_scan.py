#!/usr/bin/env python3
"""R186: 300ETF option PCR timing scan (volume PCR z-score and monthly change)."""
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
OUT = EVIDENCE / "r186_pcr_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
STATS_DIR = DATA / "research" / "r171_option_skew"
DISCOVERY_START = date(2020, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
MIN_MONTHS = 30
COST = 0.0005
FACTORS = ["pcr_volume_z", "pcr_volume_chg_1m"]
CONTROL = "qvix_z_252"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _pcr_frame() -> pl.DataFrame:
    files = sorted(STATS_DIR.glob("stats_*.parquet"))
    rows = []
    for path in files:
        df = pl.read_parquet(path)
        sub = df.filter(pl.col("合约标的代码") == "510300")
        if sub.height:
            rows.append(sub.select([
                pl.col("交易日"),
                pl.col("认沽/认购").cast(pl.Float64).alias("_pcr_raw"),
                pl.col("未平仓认沽合约数").cast(pl.Float64).alias("_oi_put"),
                pl.col("未平仓认购合约数").cast(pl.Float64).alias("_oi_call"),
            ]))
    frame = pl.concat(rows).sort("交易日").with_columns(
        (pl.col("_pcr_raw") / 100.0).alias("pcr_volume"),
        (pl.col("_oi_put") / pl.col("_oi_call")).alias("pcr_oi"),
    )
    frame = frame.with_columns(
        pl.col("pcr_volume").shift(1).alias("_prev_pcr")
    ).with_columns(
        (pl.col("pcr_volume") - pl.col("_prev_pcr")).alias("pcr_volume_chg_1m")
    )
    frame = frame.with_columns(
        pl.int_range(1, pl.len() + 1).alias("_n")
    ).with_columns([
        pl.when(pl.col("_n") >= 12)
        .then(pl.col("pcr_volume").cum_sum() / pl.col("_n"))
        .otherwise(None).alias("_mean"),
    ]).with_columns(
        pl.when(pl.col("_n") >= 12)
        .then(
            (
                (pl.col("pcr_volume") ** 2).cum_sum() / pl.col("_n")
                - (pl.col("_mean") ** 2)
            ).clip(lower_bound=0.0).sqrt()
        )
        .otherwise(None).alias("_std")
    ).with_columns(
        pl.when((pl.col("_n") >= 12) & (pl.col("_std") > 0))
        .then((pl.col("pcr_volume") - pl.col("_mean")) / pl.col("_std"))
        .otherwise(None).alias("pcr_volume_z")
    )
    return frame.select(["交易日", "pcr_volume", "pcr_oi", "pcr_volume_z", "pcr_volume_chg_1m"]).sort("交易日")


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
            rows.append({
                "rebalance_date": day,
                "entry_date": entry_day,
                "exit_date": exit_day,
                "forward_return": float(exit_ / entry - 1.0),
            })
    return pl.DataFrame(rows)


def _qvix_for_day(day: date) -> float | None:
    path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return None
    values = pl.read_parquet(path).select(CONTROL).drop_nulls()
    return float(values[CONTROL][0]) if values.height else None


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
    usable = [r for r in rows if r[factor] is not None and r[CONTROL] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    corr = _rank_corr([r[factor] for r in usable], [r[CONTROL] for r in usable])
    xs = [r[CONTROL] for r in usable]
    ys = [r[factor] for r in usable]
    n = len(usable)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return corr, None
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    residual = [y - alpha - beta * x for x, y in zip(xs, ys)]
    forward = [r["forward_return"] for r in usable]
    raw = _rank_corr(residual, forward)
    return corr, raw


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
        "strategy_mean": round(st.mean(strategy_returns), 6) if strategy_returns else None,
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
    pcr = _pcr_frame()
    forward = _index_forward(calendar, month_ends)

    pcr_map = {row["交易日"]: row for row in pcr.iter_rows(named=True)}
    rows = []
    for row in forward.iter_rows(named=True):
        day = row["rebalance_date"]
        p = pcr_map.get(day)
        if p is None:
            continue
        rows.append({
            "date": str(day),
            "forward_return": row["forward_return"],
            "pcr_volume": p["pcr_volume"],
            "pcr_oi": p["pcr_oi"],
            "pcr_volume_z": p["pcr_volume_z"],
            "pcr_volume_chg_1m": p["pcr_volume_chg_1m"],
            CONTROL: _qvix_for_day(day),
        })

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
                "qvix_corr": round(corr, 6) if corr is not None else None,
                "residual_ic": round(resid, 6) if resid is not None else None,
            }
        summary[split] = entry

    holdout_rows = [r for r in rows if r["split"] == "holdout"]
    recent_rows = holdout_rows[-12:]
    recent_lc = {}
    for factor in FACTORS:
        recent_lc[factor] = _long_cash(recent_rows, factor)

    results = {}
    for factor in FACTORS:
        d = summary["discovery"][factor]
        v = summary["validation"][factor]
        h = summary["holdout"][factor]
        d1 = (
            pcr.height >= 75
            and sum(1 for r in rows if r[factor] is not None) >= 70
            and len(rows) >= 78
        )
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
            v["qvix_corr"] is not None and abs(v["qvix_corr"]) < 0.70
            and v["residual_ic"] is not None and v["residual_ic"] >= 0.05
        )
        decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
        decision["all_pass"] = all(decision.values())
        results[factor] = decision

    result = {
        "purpose": "R186 300ETF option PCR timing scan",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "source": "SSE option_daily_stats_sse via local r171 cache",
            "months": pcr.height,
            "start": str(pcr["交易日"].min()),
            "end": str(pcr["交易日"].max()),
        },
        "method": {
            "factors": {
                "pcr_volume_z": "expanding z-score of put/call volume ratio, min 12 months, direction positive",
                "pcr_volume_chg_1m": "monthly change of put/call volume ratio, direction positive",
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
            "Only one ETF underlying (510300) and one exchange (SSE) is used.",
            "PCR is a monthly month-end snapshot; intra-month dynamics are not modeled.",
            "The expanding z-score uses all history up to T, so early months have fewer observations.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m": recent_lc, "decision": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
