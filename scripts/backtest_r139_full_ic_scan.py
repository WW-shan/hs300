#!/usr/bin/env python3
"""R139: cross-sectional IC scan over the full 64-field ext_data.

Monthly rebalance alignment: for each month end, rank stocks by the factor
value observed on that day and correlate with the next month's forward
return.  Discovery and validation are reported separately.
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
OUT = EVIDENCE / "r139_full_ic_scan.json"
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), date(2026, 10, 16))
MIN_MONTHS = 24
IC_DISC = 0.02
IC_VAL = 0.015

EXCLUDE = {"symbol", "date"}


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
    # price panel
    price_paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    prices = (
        pl.scan_parquet([str(p) for p in price_paths], extra_columns="ignore")
        .select(["symbol", "date", "close"])
        .collect()
        .sort(["symbol", "date"])
    )
    # forward 1-month return per symbol (21 trading days)
    prices = prices.with_columns(
        (pl.col("close").shift(-21).over("symbol") / pl.col("close") - 1.0).alias("fwd_21d")
    )

    # ext panel
    ext_paths = sorted((DATA / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet"))
    ext = (
        pl.scan_parquet([str(p) for p in ext_paths], hive_partitioning=True)
        .collect()
        .sort(["symbol", "date"])
    )
    fields = [c for c in ext.columns if c not in EXCLUDE]
    print(f"fields={len(fields)} ext_rows={ext.height}", flush=True)

    # month ends from price calendar
    calendar = sorted(prices["date"].unique().to_list())
    month_ends = []
    for i, day in enumerate(calendar):
        if i + 1 == len(calendar) or (calendar[i + 1].year, calendar[i + 1].month) != (day.year, day.month):
            month_ends.append(day)

    panel = ext.join(prices.select(["symbol", "date", "fwd_21d"]), on=["symbol", "date"], how="inner")

    def _ic_stats(start: date, end: date) -> dict:
        out = {}
        sub = panel.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        for field in fields:
            ics = []
            for day in month_ends:
                if day < start or day > end:
                    continue
                rows = sub.filter((pl.col("date") == day) & pl.col(field).is_not_null() & pl.col("fwd_21d").is_not_null())
                if rows.height < 30:
                    continue
                x = rows[field].cast(pl.Float64).to_list()
                y = rows["fwd_21d"].to_list()
                ic = _spearman(x, y)
                if ic is not None:
                    ics.append(ic)
            if not ics:
                out[field] = {"months": 0, "mean_ic": None}
                continue
            out[field] = {
                "months": len(ics),
                "mean_ic": round(st.mean(ics), 5),
                "std": round(st.pstdev(ics), 5) if len(ics) > 1 else None,
                "positive_rate": round(sum(v > 0 for v in ics) / len(ics), 4),
            }
        return out

    disc = _ic_stats(*DISCOVERY)
    val = _ic_stats(*VALIDATION)

    passed = []
    for field in fields:
        d, v = disc.get(field, {}), val.get(field, {})
        if d.get("mean_ic") is None or v.get("mean_ic") is None:
            continue
        if d["months"] < MIN_MONTHS or v["months"] < MIN_MONTHS:
            continue
        if abs(d["mean_ic"]) >= IC_DISC and abs(v["mean_ic"]) >= IC_VAL and d["mean_ic"] * v["mean_ic"] > 0:
            passed.append({
                "field": field,
                "discovery_ic": d["mean_ic"],
                "validation_ic": v["mean_ic"],
                "direction": "high" if d["mean_ic"] > 0 else "low",
                "discovery_months": d["months"],
                "validation_months": v["months"],
            })

    passed.sort(key=lambda r: -abs(r["validation_ic"]))
    existing = {"holder_top10_free_float_share", "stock_rel_industry_20d", "industry_mom_20d", "fund_count_low", "fund_holding_ratio_low", "margin_chg_1m_low"}
    novel = [r for r in passed if r["field"] not in existing]

    d1 = len(fields) >= 60
    g1 = len(passed) >= 3
    g2 = len(novel) >= 1
    g3 = all(r["discovery_ic"] * r["validation_ic"] > 0 for r in passed)
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R139 full 64-field IC scan on the fixed data",
        "fields": len(fields),
        "discovery": {"start": str(DISCOVERY[0]), "end": str(DISCOVERY[1])},
        "validation": {"start": str(VALIDATION[0]), "end": str(VALIDATION[1])},
        "thresholds": {"ic_discovery": IC_DISC, "ic_validation": IC_VAL, "min_months": MIN_MONTHS},
        "passed": passed,
        "novel_passed": novel,
        "discovery_stats": disc,
        "validation_stats": val,
        "decision": decision,
        "caveats": ["Overlapping forward windows; descriptive scan, not a strategy test."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("passed:", json.dumps(passed, ensure_ascii=False, indent=1), flush=True)
    print("decision:", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
