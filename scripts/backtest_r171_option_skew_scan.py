#!/usr/bin/env python3
"""R171: 300ETF 25-delta skew change as an HS300 timing signal."""
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
OUT = EVIDENCE / "r171_option_skew_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
PROBE_SCRIPT = ROOT / "scripts" / "probe_r171_option_skew.py"
DISCOVERY_START = date(2020, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "skew_change_1m"
CONTROL = "qvix_z_252"


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


def _forward_index_returns(calendar: list[date], month_ends: list[date], index: pl.DataFrame) -> pl.DataFrame:
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


def _month_shift(year: int, month: int, delta: int) -> tuple[int, int]:
    value = year * 12 + (month - 1) + delta
    return value // 12, value % 12 + 1


def _signal_frame(probe_rows: list[dict]) -> pl.DataFrame:
    by_month = {}
    for row in probe_rows:
        day = date.fromisoformat(row["date"])
        if row.get("skew_25d") is not None:
            by_month[(day.year, day.month)] = float(row["skew_25d"])
    out = []
    for row in probe_rows:
        day = date.fromisoformat(row["date"])
        skew = row.get("skew_25d")
        py, pm = _month_shift(day.year, day.month, -1)
        prev = by_month.get((py, pm))
        change = float(skew) - float(prev) if skew is not None and prev is not None else None
        out.append({
            "date": day,
            "skew_25d": float(skew) if skew is not None else None,
            FACTOR: change,
            "pcr_volume": float(row["pcr_volume"]) if row.get("pcr_volume") is not None else None,
            "pcr_oi": float(row["pcr_oi"]) if row.get("pcr_oi") is not None else None,
        })
    return pl.DataFrame(out).sort("date")


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
    forward = [r["forward_return"] for r in usable]
    raw = _rank_corr(residual, forward)
    return corr, -raw if raw is not None else None


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


def _long_cash(rows: list[dict]) -> dict:
    position = 0
    strategy_returns = []
    benchmark_returns = []
    switches = 0
    for row in rows:
        signal = row[FACTOR]
        if signal is None or row["forward_return"] is None:
            continue
        target = 1 if signal <= 0 else 0
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


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    probe = _load(PROBE_SCRIPT, "probe_r171")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if date(2020, 1, 1) <= day <= HOLDOUT_END]
    probe_rows = [probe._fetch_day(day) for day in month_ends]
    signals = _signal_frame(probe_rows)
    index = _index_daily()
    forward = _forward_index_returns(calendar, month_ends, index)
    rows = []
    for row in signals.to_dicts():
        day = row["date"]
        fwd = forward.filter(pl.col("rebalance_date") == day)
        forward_return = float(fwd["forward_return"][0]) if fwd.height else None
        qvix = _qvix_for_day(day)
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        rows.append({
            **row,
            "date": str(day),
            CONTROL: qvix,
            "forward_return": forward_return,
            "split": split,
        })

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split and r[FACTOR] is not None and r["forward_return"] is not None]
        oriented_ics = []
        hits = []
        for r in part:
            # Negative direction: high skew change predicts low return.
            if r[FACTOR] == 0:
                continue
            hits.append((r[FACTOR] < 0 and r["forward_return"] > 0) or (r[FACTOR] > 0 and r["forward_return"] < 0))
        raw_ic = _rank_corr([r[FACTOR] for r in part], [r["forward_return"] for r in part])
        oriented_ics = [-raw_ic] if raw_ic is not None else []
        summary[split] = {
            "months": len(part),
            "oriented_ic": round(oriented_ics[0], 6) if oriented_ics else None,
            "sign_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "signal_positive_rate": round(sum(r[FACTOR] > 0 for r in part) / len(part), 4) if part else None,
            "long_cash": _long_cash(part),
        }
        corr, residual_ic = _residual_ic(part)
        summary[split]["qvix_corr"] = round(corr, 6) if corr is not None else None
        summary[split]["residual_ic"] = round(residual_ic, 6) if residual_ic is not None else None

    holdout_rows = [r for r in rows if r["split"] == "holdout" and r[FACTOR] is not None and r["forward_return"] is not None]
    recent = _long_cash(holdout_rows[-12:]) if holdout_rows else {"strategy_total": None}

    skew_months = sum(r["skew_25d"] is not None for r in rows)
    change_months = sum(r[FACTOR] is not None for r in rows)
    d1 = skew_months >= 75 and change_months >= 70 and change_months / max(len(rows), 1) >= 0.90
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
    g5 = summary["validation"]["qvix_corr"] is not None and summary["validation"]["qvix_corr"] < 0.70 and summary["validation"]["residual_ic"] is not None and summary["validation"]["residual_ic"] >= 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    pcr_diagnostics = {}
    for field in ["pcr_volume", "pcr_oi"]:
        usable = [r for r in rows if r[field] is not None and r["forward_return"] is not None]
        raw = _rank_corr([r[field] for r in usable], [r["forward_return"] for r in usable])
        pcr_diagnostics[field] = {"months": len(usable), "raw_ic": round(raw, 6) if raw is not None else None}

    result = {
        "purpose": "R171 300ETF 25-delta skew change as HS300 timing signal",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "probe": "evidence/r171_option_data_probe.json",
            "cache": "data/research/r171_option_skew",
            "index": "data/kline_index_daily/000300.SH",
            "skew_months": skew_months,
            "change_months": change_months,
        },
        "method": {
            "factor": "near-month 25-delta put IV minus call IV, month-over-month change",
            "direction": "negative",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "signal <= 0 long index; signal > 0 cash",
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
        "pcr_diagnostics": pcr_diagnostics,
        "monthly_records": rows,
        "limitations": [
            "Uses 510300 ETF options as a proxy for HS300 index option sentiment.",
            "Near-month expiry is selected mechanically; no roll or option execution is simulated.",
            "The long/cash rule uses a zero threshold on skew change, not a tuned threshold.",
            "Option data are official month-end snapshots; intraday option signals are out of scope.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision, "pcr_diagnostics": pcr_diagnostics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())