#!/usr/bin/env python3
"""R189: extended-sample independent test of IF net-short change (2018-2020).

Uses CFFEX IF member position ranks (2018-2020) and the Sina HS300 index
daily series for forward returns. This window is fully out-of-sample
relative to R187 (2021-2026).
"""
from __future__ import annotations

import json
import math
import statistics as st
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r189_cffex_extended.json"
PROBE = EVIDENCE / "r189_cffex_extended_probe.json"
START = date(2018, 2, 1)
END = date(2020, 12, 31)
COST = 0.0005


def _sina_index() -> pl.DataFrame:
    cache = DATA / "research" / "r189_cffex" / "sina_hs300.parquet"
    if cache.exists():
        return pl.read_parquet(cache)
    import akshare as ak
    raw = ak.stock_zh_index_daily(symbol="sh000300")
    frame = pl.from_pandas(raw).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(cache)
    return frame


def _month_ends(dates: list[date]) -> list[date]:
    frame = pl.DataFrame({"date": dates}).with_columns([
        pl.col("date").dt.year().alias("year"),
        pl.col("date").dt.month().alias("month"),
    ])
    return (
        frame.group_by(["year", "month"])
        .agg(pl.col("date").max())
        .sort(["year", "month"])["date"]
        .to_list()
    )


def _forward_returns(index: pl.DataFrame, month_ends: list[date]) -> pl.DataFrame:
    index_dates = index["date"].to_list()
    pos = {day: i for i, day in enumerate(index_dates)}
    opens = index["open"].to_list()
    rows = []
    for day in month_ends:
        i = pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            continue
        entry = opens[i + 1]
        exit_ = opens[i + 21]
        if entry and exit_:
            rows.append({
                "rebalance_date": day,
                "forward_return": float(exit_ / entry - 1.0),
            })
    return pl.DataFrame(rows)


def _rank_corr(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) < 3 or len(values_x) != len(values_y):
        return None
    x = pl.Series(values_x).rank("average")
    y = pl.Series(values_y).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


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


def _window_stats(values: list[float], window: int = 12) -> list[float]:
    if len(values) < window:
        return []
    return [_compound(values[i : i + window]) for i in range(0, len(values) - window + 1)]


def _long_cash(rows: list[dict]) -> dict:
    position = 0
    strategy_returns = []
    benchmark_returns = []
    switches = 0
    for row in rows:
        signal = row["if_net_short_chg_1m"]
        if signal is None or row["forward_return"] is None:
            continue
        target = 1 if signal < 0 else 0
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


def main() -> int:
    probe = json.loads(PROBE.read_text(encoding="utf-8"))
    signals = pl.DataFrame(probe["monthly_records"]).with_columns(
        pl.col("date").str.to_date()
    ).sort("date")
    signals = signals.with_columns(
        pl.col("net_short_ratio").shift(1).alias("_prev")
    ).with_columns(
        (pl.col("net_short_ratio") - pl.col("_prev")).alias("if_net_short_chg_1m")
    )
    signal_map = {row["date"]: row for row in signals.iter_rows(named=True)}

    index = _sina_index()
    index = index.filter((pl.col("date") >= date(2017, 12, 1)) & (pl.col("date") <= date(2021, 2, 28)))
    month_ends = [d for d in _month_ends(index["date"].to_list()) if START <= d <= END]
    forward = _forward_returns(index, month_ends)

    rows = []
    for row in forward.iter_rows(named=True):
        day = row["rebalance_date"]
        s = signal_map.get(day)
        if s is None:
            continue
        rows.append({
            "date": str(day),
            "forward_return": row["forward_return"],
            "net_short_ratio": s["net_short_ratio"],
            "if_net_short_chg_1m": s["if_net_short_chg_1m"],
        })

    usable = [r for r in rows if r["if_net_short_chg_1m"] is not None and r["forward_return"] is not None]
    ic = _rank_corr([-r["if_net_short_chg_1m"] for r in usable], [r["forward_return"] for r in usable])
    hit = sum(1 for r in usable if (r["if_net_short_chg_1m"] < 0) == (r["forward_return"] > 0)) / len(usable) if usable else None
    lc = _long_cash(rows)

    position = 0
    strategy_returns = []
    for row in rows:
        signal = row["if_net_short_chg_1m"]
        if signal is None or row["forward_return"] is None:
            continue
        target = 1 if signal < 0 else 0
        cost = COST if target != position else 0.0
        position = target
        strategy_returns.append(target * row["forward_return"] - cost)
    rolling = _window_stats(strategy_returns)

    yearly = {}
    for r in usable:
        y = r["date"][:4]
        yearly.setdefault(y, []).append(r)
    yearly_ic = {}
    for y, part in sorted(yearly.items()):
        if len(part) >= 6:
            yearly_ic[y] = _rank_corr([-x["if_net_short_chg_1m"] for x in part], [x["forward_return"] for x in part])
    same_sign = sum(1 for v in yearly_ic.values() if v is not None and v > 0)

    coverage = len(usable) / len(rows) if rows else 0.0
    # Pre-registered D1 requires >=60 months; the local index history limits this window to ~35.
    d1 = len(usable) >= 60 and coverage >= 0.90
    g1 = ic is not None and ic >= 0.10 and hit is not None and hit >= 0.52
    g2 = (
        lc["strategy_total"] is not None and lc["benchmark_total"] is not None
        and lc["strategy_total"] > lc["benchmark_total"]
        and lc["strategy_mdd"] is not None and lc["benchmark_mdd"] is not None
        and lc["strategy_mdd"] >= lc["benchmark_mdd"] - 0.02
    )
    g3 = bool(rolling) and sum(1 for v in rolling if v > 0) / len(rolling) >= 0.60 and min(rolling) >= -0.20
    g4 = same_sign >= 2
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R189 IF net-short change extended-sample independent test (2018-2020)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "cffex": "data/research/r189_cffex (2018-01..2020-12)",
            "index": "Sina sh000300 daily (open used for forward returns)",
            "months": len(rows),
            "usable_months": len(usable),
            "coverage": round(coverage, 4),
            "start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
        },
        "ic": round(ic, 6) if ic is not None else None,
        "hit_rate": round(hit, 4) if hit is not None else None,
        "long_cash": lc,
        "rolling_12m": {
            "windows": len(rolling),
            "positive_rate": round(sum(1 for v in rolling if v > 0) / len(rolling), 4) if rolling else None,
            "worst": round(min(rolling), 6) if rolling else None,
            "median": round(st.median(rolling), 6) if rolling else None,
        },
        "yearly_ic": {k: (round(v, 6) if v is not None else None) for k, v in yearly_ic.items()},
        "decision": decision,
        "monthly_records": rows,
        "limitations": [
            "Local kline_index_daily starts 2018-01; the Sina series is used for the index open.",
            "2015-2017 CFFEX data was fetched but cannot be scored without matching index opens; the effective sample is 2018-2020.",
            "The pre-registered D1 threshold (>=60 months) is not met; the result is reported as an extended but shorter-window check.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "monthly_records"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
