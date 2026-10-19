#!/usr/bin/env python3
"""R154: conditional IC scan - gate open vs gate closed days.

For each ext field, compute monthly cross-sectional Spearman IC separately
on gate-open and gate-closed days.  Goal: find factors that work while the
A sleeve is flat (gate closed).
"""
from __future__ import annotations

import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r154_conditional_ic.json"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
RECENT = date(2024, 10, 1)
EXCLUDE = {"symbol", "date"}
MIN_MONTHS = 12
IC_MIN = 0.02


def _spearman(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 20:
        return None

    def _rank(values):
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg = (i + j) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    rx, ry = _rank(x), _rank(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main() -> int:
    price_paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    prices = (
        pl.scan_parquet([str(p) for p in price_paths], extra_columns="ignore")
        .select(["symbol", "date", "close"])
        .collect()
        .sort(["symbol", "date"])
        .with_columns((pl.col("close").shift(-21).over("symbol") / pl.col("close") - 1.0).alias("fwd_21d"))
    )
    ext_paths = sorted((DATA / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet"))
    ext = pl.scan_parquet([str(p) for p in ext_paths], hive_partitioning=True).collect().sort(["symbol", "date"])
    fields = [c for c in ext.columns if c not in EXCLUDE]
    print(f"fields={len(fields)}", flush=True)

    panel = ext.join(prices.select(["symbol", "date", "fwd_21d"]), on=["symbol", "date"], how="inner")
    panel = panel.with_columns(
        (
            (pl.col("equity_risk_premium") >= 5.5)
            & (pl.col("credit_spread_5y_low") >= -0.8)
            & (pl.col("term_spread_10y_2y") >= 0.3)
        ).alias("gate_open")
    )

    calendar = sorted(prices["date"].unique().to_list())
    month_ends = []
    for i, day in enumerate(calendar):
        if i + 1 == len(calendar) or (calendar[i + 1].year, calendar[i + 1].month) != (day.year, day.month):
            month_ends.append(day)

    def _ic(field: str, gate: bool, start: date, end: date) -> dict:
        sub = panel.filter(
            (pl.col("date") >= start) & (pl.col("date") <= end) & (pl.col("gate_open") == gate)
        )
        ics = []
        used_months = []
        for day in month_ends:
            if day < start or day > end:
                continue
            rows = sub.filter((pl.col("date") == day) & pl.col(field).is_not_null() & pl.col("fwd_21d").is_not_null())
            if rows.height < 30:
                continue
            ic = _spearman(rows[field].cast(pl.Float64).to_list(), rows["fwd_21d"].to_list())
            if ic is not None:
                ics.append(ic)
                used_months.append(day)
        if not ics:
            return {"months": 0, "mean_ic": None}
        return {
            "months": len(ics),
            "mean_ic": round(st.mean(ics), 5),
            "positive_rate": round(sum(v > 0 for v in ics) / len(ics), 4),
        }

    results = {}
    for field in fields:
        closed = _ic(field, False, START, END)
        opened = _ic(field, True, START, END)
        closed_recent = _ic(field, False, RECENT, END)
        results[field] = {
            "gate_closed": closed,
            "gate_open": opened,
            "gate_closed_recent": closed_recent,
        }

    closed_pass = [
        {"field": f, **v["gate_closed"], "recent_ic": v["gate_closed_recent"].get("mean_ic"), "open_ic": v["gate_open"].get("mean_ic")}
        for f, v in results.items()
        if v["gate_closed"].get("mean_ic") is not None
        and v["gate_closed"]["months"] >= MIN_MONTHS
        and abs(v["gate_closed"]["mean_ic"]) >= IC_MIN
    ]
    closed_pass.sort(key=lambda r: -abs(r["mean_ic"]))

    open_pass = [
        f for f, v in results.items()
        if v["gate_open"].get("mean_ic") is not None
        and v["gate_open"]["months"] >= MIN_MONTHS
        and abs(v["gate_open"]["mean_ic"]) >= IC_MIN
    ]

    recent_same_sign = [
        r for r in closed_pass
        if r["recent_ic"] is not None and r["recent_ic"] * r["mean_ic"] > 0 and abs(r["recent_ic"]) >= 0.015
    ]

    d1 = len(fields) >= 60 and any(v["gate_closed"]["months"] >= MIN_MONTHS for v in results.values())
    g1 = len(closed_pass) >= 2
    g2 = len(recent_same_sign) >= 1
    g3 = set(r["field"] for r in closed_pass) != set(open_pass)
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R154 conditional IC: gate open vs closed",
        "fields": len(fields),
        "gate_closed_pass": closed_pass,
        "gate_open_pass": open_pass,
        "recent_same_sign": recent_same_sign,
        "all_results": results,
        "decision": decision,
        "caveats": ["Gate-closed months are fewer; overlapping forward windows."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("gate-closed pass:", json.dumps(closed_pass, ensure_ascii=False, indent=1), flush=True)
    print("recent same sign:", json.dumps(recent_same_sign, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
