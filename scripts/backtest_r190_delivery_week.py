#!/usr/bin/env python3
"""R190: IF delivery-week calendar effect on HS300 index."""
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
OUT = EVIDENCE / "r190_delivery_week.json"
DISCOVERY_START = date(2018, 1, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2026, 10, 16)
WINDOW = 4
COST_ROUND_TRIP = 0.0028


def _index() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def _delivery_dates(calendar: list[date]) -> list[date]:
    by_month: dict[tuple[int, int], list[date]] = {}
    for day in calendar:
        by_month.setdefault((day.year, day.month), []).append(day)
    out = []
    for (year, month), days in sorted(by_month.items()):
        fridays = [d for d in days if d.weekday() == 4]
        if len(fridays) < 3:
            continue
        third = fridays[2]
        if third in days:
            out.append(third)
        else:
            later = [d for d in days if d > third]
            if later:
                out.append(later[0])
    return out


def _daily_returns(index: pl.DataFrame) -> tuple[list[date], list[float]]:
    dates = index["date"].to_list()
    closes = index["close"].to_list()
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    return dates[1:], rets


def _label(dates: list[date], deliveries: list[date]) -> list[str]:
    pos = {d: i for i, d in enumerate(dates)}
    labels = ["other"] * len(dates)
    for d in deliveries:
        i = pos.get(d)
        if i is None:
            continue
        for j in range(max(0, i - (WINDOW - 1)), i + 1):
            labels[j] = "delivery_week"
        for j in range(i + 1, min(len(dates), i + 1 + WINDOW)):
            if labels[j] == "other":
                labels[j] = "post_delivery"
    return labels


def _mean_t(values: list[float]) -> tuple[float | None, float | None, int]:
    if len(values) < 3:
        return None, None, len(values)
    mean = st.mean(values)
    sd = st.stdev(values)
    t = mean / (sd / math.sqrt(len(values))) if sd > 0 else None
    return mean, t, len(values)


def _bh_qvalues(pvalues: list[float]) -> list[float]:
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    q = [0.0] * n
    prev = 1.0
    for rank, idx in enumerate(reversed(order), start=1):
        k = n - rank + 1
        value = min(prev, pvalues[idx] * n / k)
        q[idx] = value
        prev = value
    return q


def _t_pvalue(t: float | None, n: int) -> float | None:
    if t is None or n < 3:
        return None
    z = abs(t)
    return math.erfc(z / math.sqrt(2.0))


def main() -> int:
    index = _index()
    calendar = index["date"].to_list()
    deliveries = _delivery_dates(calendar)
    dates, rets = _daily_returns(index)
    labels = _label(dates, deliveries)

    records = []
    for d, r, lab in zip(dates, rets, labels, strict=True):
        records.append({"date": str(d), "return": r, "label": lab})

    def subset(start: date, end: date) -> list[dict]:
        return [x for x in records if start <= date.fromisoformat(x["date"]) <= end]

    def compare(part: list[dict]) -> dict:
        dw = [x["return"] for x in part if x["label"] == "delivery_week"]
        pd_ = [x["return"] for x in part if x["label"] == "post_delivery"]
        other = [x["return"] for x in part if x["label"] == "other"]
        m_dw, t_dw, n_dw = _mean_t(dw)
        m_pd, t_pd, n_pd = _mean_t(pd_)
        m_ot, t_ot, n_ot = _mean_t(other)
        diff = (m_dw - m_ot) if m_dw is not None and m_ot is not None else None
        t_diff = None
        if len(dw) >= 3 and len(other) >= 3:
            sd_dw = st.stdev(dw)
            sd_ot = st.stdev(other)
            se = math.sqrt(sd_dw ** 2 / len(dw) + sd_ot ** 2 / len(other))
            t_diff = (m_dw - m_ot) / se if se > 0 else None
        diff_post = (m_dw - m_pd) if m_dw is not None and m_pd is not None else None
        t_post = None
        if len(dw) >= 3 and len(pd_) >= 3:
            sd_dw = st.stdev(dw)
            sd_pd = st.stdev(pd_)
            se = math.sqrt(sd_dw ** 2 / len(dw) + sd_pd ** 2 / len(pd_))
            t_post = (m_dw - m_pd) / se if se > 0 else None
        return {
            "delivery_week": {"mean": m_dw, "t": t_dw, "n": n_dw},
            "post_delivery": {"mean": m_pd, "t": t_pd, "n": n_pd},
            "other": {"mean": m_ot, "t": t_ot, "n": n_ot},
            "diff_vs_other": diff,
            "t_diff_vs_other": t_diff,
            "diff_vs_post": diff_post,
            "t_diff_vs_post": t_post,
        }

    disc = compare(subset(DISCOVERY_START, DISCOVERY_END))
    val = compare(subset(VALIDATION_START, VALIDATION_END))
    full = compare(records)

    p_other = _t_pvalue(disc["t_diff_vs_other"], disc["delivery_week"]["n"] + disc["other"]["n"])
    p_post = _t_pvalue(disc["t_diff_vs_post"], disc["delivery_week"]["n"] + disc["post_delivery"]["n"])
    q = _bh_qvalues([p if p is not None else 1.0 for p in [p_other, p_post]])

    d1 = len(deliveries) >= 90 and len(deliveries) == len({(d.year, d.month) for d in deliveries})
    g1 = (
        disc["diff_vs_other"] is not None and disc["diff_vs_other"] <= -0.0005
        and disc["t_diff_vs_other"] is not None and disc["t_diff_vs_other"] <= -2.0
    )
    g2 = (
        val["diff_vs_other"] is not None and disc["diff_vs_other"] is not None
        and val["diff_vs_other"] * disc["diff_vs_other"] > 0
        and val["t_diff_vs_other"] is not None and val["t_diff_vs_other"] <= -1.5
    )
    g3 = (
        disc["diff_vs_post"] is not None and disc["diff_vs_post"] <= -0.0010
        and val["diff_vs_post"] is not None and disc["diff_vs_post"] * val["diff_vs_post"] > 0
    )
    g4 = disc["diff_vs_other"] is not None and abs(disc["diff_vs_other"]) >= COST_ROUND_TRIP / WINDOW
    g5 = q[0] <= 0.10
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R190 IF delivery-week calendar effect on HS300 index",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "index": "data/kline_index_daily 000300.SH",
            "days": len(records),
            "delivery_dates": len(deliveries),
            "start": records[0]["date"],
            "end": records[-1]["date"],
        },
        "method": {
            "delivery_date": "third Friday of each month, rolled forward to the next trading day",
            "delivery_week": f"delivery date and prior {WINDOW - 1} trading days",
            "post_delivery": f"{WINDOW} trading days after the delivery date",
            "test": "Welch t-test on daily return difference; BH-FDR over 2 discovery tests",
        },
        "discovery": disc,
        "validation": val,
        "full": full,
        "fdr": {"p_vs_other": p_other, "p_vs_post": p_post, "q_vs_other": q[0], "q_vs_post": q[1]},
        "decision": decision,
        "limitations": [
            "Normal-approximation p-values; daily returns are not i.i.d. but the sample is large.",
            "Delivery dates are derived from the local trading calendar, not an official CFFEX calendar.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
