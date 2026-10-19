#!/usr/bin/env python3
"""R135: the PIT honest candidate versus the HS300 index (000300.SH).

Buy-and-hold index comparison across the full period, the recent year and
the out-of-sample segment, plus rolling 1-year positive rates for both.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r135_vs_index.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
R128_CACHE = DATA / "research" / "r128_cache"
R132_CACHE = DATA / "research" / "r132_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _index_curve() -> pl.DataFrame:
    parts = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(p) for p in parts])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    return frame


def _stats_from_values(values: list[float]) -> dict:
    if len(values) < 2:
        return {"days": len(values)}
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    annual = (1.0 + total) ** (252.0 / len(returns)) - 1.0
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(252.0) if sd > 0 else 0.0
    peak = values[0]
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {
        "total_return": round(total, 6),
        "annual": round(annual, 6),
        "sharpe": round(sharpe, 4),
        "mdd": round(mdd, 6),
        "days": len(values),
    }


def _rolling_positive(values: list[float]) -> dict:
    recs = []
    for start in range(0, len(values) - WINDOW + 1):
        window = values[start : start + WINDOW]
        recs.append(window[-1] / window[0] - 1.0)
    if not recs:
        return {"windows": 0}
    return {
        "windows": len(recs),
        "positive_rate": round(sum(v > 0 for v in recs) / len(recs), 4),
        "median": round(st.median(recs), 4),
        "worst": round(min(recs), 4),
    }


def _slice(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    return curve.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    index = _index_curve()

    # full period candidate from the longest phase
    key = "2021-06-01"
    a = pl.read_parquet(
        R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    b = pl.read_parquet(
        R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk, _ = r112._combine(a, b, "risk")
    cand_full = r114._vol_target(risk)["curve"]

    # recent year
    key_r = "2025-10-14"
    a_r = pl.read_parquet(
        R125_CACHE / f"{A_SLEEVE}_{key_r}_{r112.END.isoformat()}_3x.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    b_r = pl.read_parquet(
        R119_CACHE / f"{B_SLEEVE}_{key_r}_{r112.END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_r, _ = r112._combine(a_r, b_r, "risk")
    cand_recent = r114._vol_target(risk_r)["curve"]

    # out-of-sample segment candidate from R132 cache
    a_oos = pl.read_parquet(
        R128_CACHE / f"{A_SLEEVE}_2020-12-31_{r112.END.isoformat()}.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    b_oos = pl.read_parquet(
        R132_CACHE / f"{B_SLEEVE}_2020-12-31_{r112.END.isoformat()}_pit.parquet"
    ).with_columns(pl.col("date").cast(pl.Date))
    risk_oos, _ = r112._combine(a_oos, b_oos, "risk")
    cand_oos_full = r114._vol_target(risk_oos)["curve"]

    def _compare(label: str, cand: pl.DataFrame, start: date, end: date) -> dict:
        c = _slice(cand, start, end)
        i = _slice(index, start, end)
        joined = (
            c.rename({"value": "cand"})
            .join(i.rename({"close": "idx"}), on="date", how="inner")
            .sort("date")
        )
        cvals = (joined["cand"] / joined["cand"][0]).to_list()
        ivals = (joined["idx"] / joined["idx"][0]).to_list()
        out = {
            "window": [str(start), str(end)],
            "days": joined.height,
            "candidate": _stats_from_values(cvals),
            "index": _stats_from_values(ivals),
            "rolling_1y_candidate": _rolling_positive(cvals),
            "rolling_1y_index": _rolling_positive(ivals),
        }
        print(label, json.dumps(out, ensure_ascii=False), flush=True)
        return out

    full = _compare("full", cand_full, date(2021, 6, 1), r112.END)
    recent = _compare("recent", cand_recent, date(2025, 10, 14), r112.END)
    oos = _compare("oos", cand_oos_full, date(2020, 12, 31), date(2021, 6, 1))

    d1 = full["days"] > 1000 and recent["days"] > 200 and oos["days"] > 80
    g1 = full["candidate"]["total_return"] > full["index"]["total_return"]
    g2 = full["candidate"]["mdd"] >= full["index"]["mdd"] + 0.10
    g3 = recent["candidate"]["total_return"] > 0 and recent["candidate"]["mdd"] >= -0.15
    g4 = full["rolling_1y_candidate"]["positive_rate"] >= full["rolling_1y_index"]["positive_rate"] + 0.10
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R135 PIT honest candidate versus HS300 index",
        "full": full,
        "recent": recent,
        "out_of_sample": oos,
        "decision": decision,
        "caveats": [
            "Index is price-only (no dividends); the candidate total-return curve includes dividends implicitly through adjusted prices.",
            "Windows overlap; descriptive only.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
