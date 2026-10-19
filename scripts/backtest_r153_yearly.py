#!/usr/bin/env python3
"""R153: yearly decomposition of the candidate versus the index."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r153_yearly.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
PHASE = "2021-06-01"
END = "2026-10-16"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{PHASE}_{END}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
    b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{PHASE}_{END}.parquet").with_columns(pl.col("date").cast(pl.Date))
    risk, _ = r112._combine(a, b, "risk")
    cand = r114._vol_target(risk)["curve"].sort("date")

    idx = (
        pl.scan_parquet([str(p) for p in sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    joined = cand.join(idx, on="date", how="inner").sort("date")
    joined = joined.with_columns(pl.col("date").dt.year().alias("year"))

    years = {}
    for (year,), part in joined.group_by("year", maintain_order=True):
        cvals = part["value"].to_list()
        ivals = part["close"].to_list()
        peak = cvals[0]
        mdd = 0.0
        for v in cvals:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        years[str(year)] = {
            "days": part.height,
            "candidate_return": round(cvals[-1] / cvals[0] - 1.0, 4),
            "index_return": round(ivals[-1] / ivals[0] - 1.0, 4),
            "candidate_mdd": round(mdd, 4),
        }
        print(f"{year}: cand={years[str(year)]['candidate_return']:+.2%} idx={years[str(year)]['index_return']:+.2%} mdd={years[str(year)]['candidate_mdd']:.2%}", flush=True)

    d1 = len(years) == 6
    g1 = sum(1 for v in years.values() if v["candidate_return"] > 0) >= 5
    g2 = all(v["candidate_return"] > 0 for v in years.values() if v["index_return"] < 0)
    g3 = all(v["candidate_return"] >= -0.10 for v in years.values())
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R153 yearly decomposition",
        "years": years,
        "decision": decision,
        "caveats": ["2021 and 2026 are partial years.", "Single phase."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
