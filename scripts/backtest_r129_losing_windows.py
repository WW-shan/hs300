#!/usr/bin/env python3
"""R129: dissect the losing rolling 1-year windows of the R126 candidate.

For every overlapping 252-day window on the candidate curve, record the
window return, MDD, end date, and (when available) the concurrent A-sleeve
return, B-sleeve return, and candidate realized volatility.  Then test
whether losing windows cluster in time or in a high-volatility state.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r129_losing_windows.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R112_CACHE = DATA / "research" / "r112_cache"
WINDOW = 252
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[index]


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    windows = []
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(
            R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(
            R112_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
        ).with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        cand = r114._vol_target(risk)["curve"]
        cand_values = cand["value"].to_list()
        cand_dates = cand["date"].to_list()
        rets = [cand_values[i] / cand_values[i - 1] - 1.0 for i in range(1, len(cand_values))]
        a_values = a["value"].to_list()
        b_values = b["value"].to_list()
        a_map = dict(zip(a["date"].to_list(), a_values))
        b_map = dict(zip(b["date"].to_list(), b_values))
        if len(cand_values) < WINDOW:
            continue
        for start in range(0, len(cand_values) - WINDOW + 1):
            w = cand_values[start : start + WINDOW]
            wdates = cand_dates[start : start + WINDOW]
            window_ret = w[-1] / w[0] - 1.0
            peak = w[0]
            mdd = 0.0
            for v in w:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1.0)
            vol = st.pstdev(rets[start : start + WINDOW]) * math.sqrt(252.0)
            if start >= 60:
                trailing_vol = st.pstdev(rets[start - 60 : start]) * math.sqrt(252.0)
            else:
                trailing_vol = None
            a_ret = a_map[wdates[-1]] / a_map[wdates[0]] - 1.0
            b_ret = b_map[wdates[-1]] / b_map[wdates[0]] - 1.0
            windows.append({
                "phase": key,
                "start": wdates[0].isoformat(),
                "end": wdates[-1].isoformat(),
                "return": window_ret,
                "mdd": mdd,
                "realized_vol": vol,
                "trailing_vol_at_start": trailing_vol,
                "a_return": a_ret,
                "b_return": b_ret,
            })

    losing = [w for w in windows if w["return"] <= 0]
    winning = [w for w in windows if w["return"] > 0]

    end_months = Counter(w["end"][:7] for w in losing)
    top_months = end_months.most_common(12)
    start_months = Counter(w["start"][:7] for w in losing)
    top_start_months = start_months.most_common(12)

    # contiguity: what share of losing windows end within the densest 12-month span
    def _plus_year(s: str) -> str:
        y, m, d = map(int, s.split("-"))
        return f"{y + 1:04d}-{m:02d}-{d:02d}"

    ends = sorted(w["end"] for w in losing)
    best_share = 0.0
    best_span = None
    for i in range(len(ends)):
        j = i
        while j < len(ends) and ends[j] <= _plus_year(ends[i]):
            j += 1
        share = (j - i) / len(ends)
        if share > best_share:
            best_share = share
            best_span = (ends[i], ends[j - 1] if j > i else ends[i])

    vol_all = [w["realized_vol"] for w in windows]
    vol_p80 = _quantile(vol_all, 0.80)
    high_vol_windows = [w for w in windows if w["realized_vol"] > vol_p80]
    low_vol_windows = [w for w in windows if w["realized_vol"] <= vol_p80]
    high_pos = sum(w["return"] > 0 for w in high_vol_windows) / len(high_vol_windows)
    low_pos = sum(w["return"] > 0 for w in low_vol_windows) / len(low_vol_windows)
    all_pos = sum(w["return"] > 0 for w in windows) / len(windows)

    def _summ(ws: list[dict]) -> dict:
        if not ws:
            return {"n": 0}
        rs = [w["return"] for w in ws]
        return {
            "n": len(ws),
            "positive_rate": round(sum(v > 0 for v in rs) / len(rs), 4),
            "return_p05": round(_quantile(rs, 0.05), 4),
            "return_median": round(st.median(rs), 4),
            "return_worst": round(min(rs), 4),
        }

    # exploratory: split by vol observable at window start (no look-ahead)
    tv_windows = [w for w in windows if w["trailing_vol_at_start"] is not None]
    tv_values = [w["trailing_vol_at_start"] for w in tv_windows]
    tv_p80 = _quantile(tv_values, 0.80)
    tv_high = [w for w in tv_windows if w["trailing_vol_at_start"] > tv_p80]
    tv_low = [w for w in tv_windows if w["trailing_vol_at_start"] <= tv_p80]
    trailing_split = {
        "vol_p80": round(tv_p80, 4),
        "high_trailing": _summ(tv_high),
        "low_trailing": _summ(tv_low),
    }

    d1 = len(windows) == 4927
    g1 = best_share >= 0.70
    g2 = high_pos < all_pos - 0.10
    g3 = low_pos >= 0.95 if g2 else False

    result = {
        "purpose": "R129 losing-window anatomy for the R126 candidate",
        "window_days": WINDOW,
        "total_windows": len(windows),
        "losing_windows": len(losing),
        "losing_share": round(len(losing) / len(windows), 4),
        "losing_end_months_top": top_months,
        "losing_start_months_top": top_start_months,
        "densest_12m_share_of_losing": round(best_share, 4),
        "densest_12m_span": best_span,
        "losing_sample": sorted(losing, key=lambda w: w["return"])[:40],
        "exploratory_trailing_vol_split": trailing_split,
        "state_split": {
            "vol_p80": round(vol_p80, 4),
            "high_vol": _summ(high_vol_windows),
            "low_vol": _summ(low_vol_windows),
            "all": _summ(windows),
        },
        "decision": {"D1": d1, "G1": g1, "G2": g2, "G3": g3},
        "caveats": [
            "Windows overlap heavily; counts are not independent samples.",
            "The volatility split is descriptive and in-sample.",
        ],
    }
    result["decision"]["all_pass"] = all(result["decision"].values())
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("total windows:", len(windows), "losing:", len(losing), f"({len(losing)/len(windows):.2%})", flush=True)
    print("losing end months top:", top_months, flush=True)
    print("losing start months top:", top_start_months, flush=True)
    print("densest 12m share:", best_share, best_span, flush=True)
    print("state split:", json.dumps(result["state_split"], ensure_ascii=False), flush=True)
    print("exploratory trailing split:", json.dumps(trailing_split, ensure_ascii=False), flush=True)
    print("decision:", result["decision"], flush=True)
    print("worst 10:")
    for w in sorted(losing, key=lambda x: x["return"])[:10]:
        print(" ", w["start"], "->", w["end"], f"{w['return']:+.2%}", f"mdd={w['mdd']:.2%}", f"a={w['a_return']:+.2%}", f"b={w['b_return']:+.2%}", f"vol={w['realized_vol']:.1%}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
