#!/usr/bin/env python3
"""R158: apply the 15% vol target to the extended-period B sleeve."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r158_extended_vol_target.json"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _stats(values: list[float]) -> dict:
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    annual = (1.0 + total) ** (252.0 / len(rets)) - 1.0
    sd = st.pstdev(rets)
    sharpe = st.mean(rets) / sd * math.sqrt(252.0) if sd > 0 else 0.0
    peak = values[0]
    mdd = 0.0
    for v in values:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {"total_return": round(total, 6), "annual": round(annual, 6), "sharpe": round(sharpe, 4), "mdd": round(mdd, 6)}


def main() -> int:
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    b = pl.read_parquet(DATA / "research" / "r157_cache" / f"{B_SLEEVE}_2018-01-02_2020-12-31.parquet").with_columns(pl.col("date").cast(pl.Date)).sort("date")
    overlay = r114._vol_target(b)
    cand = overlay["curve"]

    idx = (
        pl.scan_parquet([str(p) for p in sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    joined = cand.join(idx, on="date", how="inner").sort("date")
    cvals = joined["value"].to_list()
    ivals = joined["close"].to_list()

    b_stats = _stats(b["value"].to_list())
    c_stats = _stats(cvals)
    i_stats = _stats(ivals)

    win = 252
    recs = [cvals[i + win] / cvals[i] - 1.0 for i in range(len(cvals) - win)]
    c_pos = round(sum(v > 0 for v in recs) / len(recs), 4) if recs else None

    d1 = cand.height > 700 and len(overlay["weight_records"]) > 0
    g1 = c_stats["mdd"] >= -0.20
    g2 = c_stats["mdd"] >= i_stats["mdd"] + 0.10
    g3 = c_stats["total_return"] >= i_stats["total_return"]
    g4 = c_pos is not None and c_pos >= 0.70
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R158 extended-period B sleeve with 15% vol target",
        "B_raw": b_stats,
        "B_vol_target_15": c_stats,
        "index": i_stats,
        "B_vol_target_rolling_1y_positive": c_pos,
        "annual_turnover": round(overlay["annual_turnover"], 4),
        "decision": decision,
        "caveats": ["Single window; the A sleeve is absent before 2020Q1."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("B raw:", json.dumps(b_stats, ensure_ascii=False), flush=True)
    print("B+vt:", json.dumps(c_stats, ensure_ascii=False), flush=True)
    print("index:", json.dumps(i_stats, ensure_ascii=False), flush=True)
    print("roll pos:", c_pos, "turnover:", round(overlay["annual_turnover"], 3), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
