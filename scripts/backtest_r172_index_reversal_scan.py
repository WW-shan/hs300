#!/usr/bin/env python3
"""R172: HS300 20-day time-series reversal scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r172_index_reversal_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2018, 2, 1)
DISCOVERY_END = date(2021, 12, 31)
VALIDATION_START = date(2022, 1, 1)
VALIDATION_END = date(2023, 12, 31)
HOLDOUT_START = date(2024, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "hs300_reversal_20d"
CONTROL = "trend_60d"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open", "close"])
        .collect()
        .sort("date")
    )


def _rank_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x = pl.Series(xs).rank("average")
    y = pl.Series(ys).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(rows: list[dict]) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[FACTOR] is not None and r[CONTROL] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    corr = _rank_corr([r[FACTOR] for r in usable], [r[CONTROL] for r in usable])
    xs = [r[CONTROL] for r in usable]
    ys = [r[FACTOR] for r in usable]
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
    raw = _rank_corr(residual, [r["forward_return"] for r in usable])
    return corr, raw


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


def _long_cash(rows: list[dict]) -> dict:
    position = 0
    strategy = []
    benchmark = []
    switches = 0
    for row in rows:
        if row[FACTOR] is None or row["forward_return"] is None:
            continue
        target = 1 if row[FACTOR] > 0 else 0
        cost = COST if target != position else 0.0
        switches += int(target != position)
        position = target
        strategy.append(target * row["forward_return"] - cost)
        benchmark.append(row["forward_return"])
    return {
        "months": len(strategy),
        "strategy_total": round(_compound(strategy), 6) if strategy else None,
        "benchmark_total": round(_compound(benchmark), 6) if benchmark else None,
        "strategy_mdd": round(_mdd(strategy), 6) if strategy else None,
        "benchmark_mdd": round(_mdd(benchmark), 6) if benchmark else None,
        "strategy_mean": round(st.mean(strategy), 6) if strategy else None,
        "switches": switches,
    }


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}
    rows = []
    for day in month_ends:
        i = index_pos.get(day)
        if i is None or i < 60 or i + 21 >= len(index_dates):
            continue
        entry_day = index_dates[i + 1]
        exit_day = index_dates[i + 21]
        entry_open = index.filter(pl.col("date") == entry_day)["open"].item()
        exit_open = index.filter(pl.col("date") == exit_day)["open"].item()
        close_now = index["close"][i]
        close_20 = index["close"][i - 20]
        close_60 = index["close"][i - 60]
        forward_return = float(exit_open / entry_open - 1.0)
        factor = float(-(close_now / close_20 - 1.0))
        control = float(close_now / close_60 - 1.0)
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        rows.append({
            "date": str(day),
            "split": split,
            FACTOR: factor,
            CONTROL: control,
            "forward_return": forward_return,
        })

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split and r[FACTOR] is not None and r["forward_return"] is not None]
        raw_ic = _rank_corr([r[FACTOR] for r in part], [r["forward_return"] for r in part])
        hits = [
            (r[FACTOR] < 0 and r["forward_return"] > 0) or (r[FACTOR] > 0 and r["forward_return"] < 0)
            for r in part if r[FACTOR] != 0
        ]
        corr, residual = _residual_ic(part)
        summary[split] = {
            "months": len(part),
            "oriented_ic": round(raw_ic, 6) if raw_ic is not None else None,
            "sign_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "signal_positive_rate": round(sum(r[FACTOR] > 0 for r in part) / len(part), 4) if part else None,
            "trend_corr": round(corr, 6) if corr is not None else None,
            "residual_ic": round(residual, 6) if residual is not None else None,
            "long_cash": _long_cash(part),
        }
    holdout = [r for r in rows if r["split"] == "holdout"]
    recent = _long_cash(holdout[-12:]) if holdout else {"strategy_total": None}

    d1 = len(rows) >= 100
    g1 = summary["discovery"]["oriented_ic"] is not None and summary["discovery"]["oriented_ic"] >= 0.15 and summary["discovery"]["sign_hit_rate"] >= 0.55
    g2 = summary["validation"]["oriented_ic"] is not None and summary["validation"]["oriented_ic"] >= 0.10 and summary["validation"]["sign_hit_rate"] >= 0.52
    g3 = summary["holdout"]["oriented_ic"] is not None and summary["holdout"]["oriented_ic"] >= 0.05 and summary["holdout"]["sign_hit_rate"] >= 0.52 and recent["strategy_total"] is not None and recent["strategy_total"] > 0
    g4 = all(
        summary[s]["long_cash"]["strategy_total"] is not None
        and summary[s]["long_cash"]["benchmark_total"] is not None
        and summary[s]["long_cash"]["strategy_total"] >= summary[s]["long_cash"]["benchmark_total"] - 0.02
        and summary[s]["long_cash"]["strategy_mdd"] is not None
        and summary[s]["long_cash"]["benchmark_mdd"] is not None
        and summary[s]["long_cash"]["strategy_mdd"] >= summary[s]["long_cash"]["benchmark_mdd"] + 0.02
        for s in ["validation", "holdout"]
    )
    g5 = summary["validation"]["trend_corr"] is not None and summary["validation"]["trend_corr"] < 0.70 and summary["validation"]["residual_ic"] is not None and summary["validation"]["residual_ic"] >= 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R172 HS300 20-day time-series reversal",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {"index": "data/kline_index_daily/000300.SH", "months": len(rows)},
        "method": {
            "factor": "negative past 20 trading-day return",
            "direction": "positive",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "factor > 0 long; otherwise cash",
            "cost_per_switch": COST,
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_cash": recent,
        "decision": decision,
        "monthly_records": rows,
        "limitations": [
            "Single market index timing test; no cross-sectional stock selection.",
            "Monthly overlapping forward returns.",
            "Zero threshold on past 20-day return is fixed ex ante, not tuned.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())