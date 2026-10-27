#!/usr/bin/env python3
"""R263: R131 core + R257/tp20 satellite core-satellite combination."""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r263_core_satellite.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R159_CACHE = DATA / "research" / "r159_cache"
R262_CACHE = DATA / "research" / "r262_cache"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
SAT_SID = "ai_hs300_quality_relative_strength_h20_sl20"
SEGMENTS = [(date(2023, 5, 4), date(2024, 12, 31)), (date(2025, 1, 2), END)]
VARIANTS = {"core70_sat30": (0.70, 0.30), "core80_sat20": (0.80, 0.20)}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _slice_rebase(frame: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    out = frame.filter((pl.col("date") >= start) & (pl.col("date") <= end)).sort("date")
    first = out["value"][0]
    return out.with_columns((pl.col("value") / first).alias("value"))


def _monthly_combine(core: pl.DataFrame, sat: pl.DataFrame, w_core: float, w_sat: float) -> pl.DataFrame:
    joined = (
        core.rename({"value": "core"})
        .join(sat.rename({"value": "sat"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    cores = joined["core"].to_list()
    sats = joined["sat"].to_list()
    values = [1.0]
    v_core = w_core
    v_sat = w_sat
    prev_month = (dates[0].year, dates[0].month)
    for i in range(1, len(dates)):
        month = (dates[i].year, dates[i].month)
        if month != prev_month:
            total = v_core + v_sat
            v_core = total * w_core
            v_sat = total * w_sat
            prev_month = month
        r_core = cores[i] / cores[i - 1] - 1.0
        r_sat = sats[i] / sats[i - 1] - 1.0
        v_core *= 1.0 + r_core
        v_sat *= 1.0 + r_sat
        values.append(v_core + v_sat)
    return pl.DataFrame({"date": dates, "value": values})


def _rolling(frame: pl.DataFrame, r229):
    return r229._result.__wrapped__ if False else None


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r263")
    r114 = _load(R114_SCRIPT, "r114_r263")
    r229 = _load(R229_SCRIPT, "r229_r263")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(R159_CACHE / f"{A_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(
        pl.col("date").cast(pl.Date)
    )
    b = pl.read_parquet(R159_CACHE / f"{B_SLEEVE}_2020-06-01_2026-10-16.parquet").with_columns(
        pl.col("date").cast(pl.Date)
    )
    risk, _ = r112._combine(a, b, "risk")
    core_full = r114._vol_target(risk)["curve"]
    core = _slice_rebase(core_full, START, END)

    sat_curves = []
    for start, end in SEGMENTS:
        path = R262_CACHE / f"{SAT_SID}_tp20_3x_{start.isoformat()}_{end.isoformat()}.parquet"
        sat_curves.append(pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date"))
    sat_full = r229._stitch(sat_curves)
    sat = _slice_rebase(sat_full, START, END)

    core_result = r229._result(r112, core)
    core_result["discovery_2023_2024"] = core_result.pop("discovery_2022_2024")
    sat_result = r229._result(r112, sat)
    sat_result["discovery_2023_2024"] = sat_result.pop("discovery_2022_2024")

    combos = {}
    for name, (wc, ws) in VARIANTS.items():
        curve = _monthly_combine(core, sat, wc, ws)
        result = r229._result(r112, curve)
        result["discovery_2023_2024"] = result.pop("discovery_2022_2024")
        combos[name] = {"weights": {"core": wc, "satellite": ws}, "curve": curve, "result": result}

    r262 = json.loads((EVIDENCE / "r262_take_profit_concentration.json").read_text(encoding="utf-8"))
    sat_top1 = r262["variants"]["tp20"]["concentration"]["full"]["top1_net_share"]

    decisions = {}
    for name, combo in combos.items():
        ws = combo["weights"]["satellite"]
        res = combo["result"]
        g1 = res["full"]["total_return"] > core_result["full"]["total_return"]
        g2 = res["full"]["mdd"] >= core_result["full"]["mdd"] - 0.05
        g3 = (res["rolling_1y"]["positive_rate"] or 0.0) >= (core_result["rolling_1y"]["positive_rate"] or 0.0)
        g4 = (res["recent_12m"]["total_return"] or 0.0) >= (core_result["recent_12m"]["total_return"] or 0.0)
        g5 = res["full"]["sharpe"] >= core_result["full"]["sharpe"]
        g6 = ws * sat_top1 <= 0.15
        decisions[name] = {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
        decisions[name]["all_pass"] = all(decisions[name].values())

    payload = {
        "round": "R263",
        "purpose": "Core-satellite combination of R131 dual-sleeve core and R257/tp20 satellite.",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r263_preregistration.json",
        "window": {"start": str(START), "end": str(END)},
        "construction": {
            "core": "R131 A/B risk parity + 15% vol target (r159 3x cache)",
            "satellite": "R257 tp20 (take_profit=0.20, max_hold=20, 3x)",
            "rebalance": "monthly to fixed weights",
            "variants": VARIANTS,
        },
        "core_alone": core_result,
        "satellite_alone": sat_result,
        "satellite_top1_net_share": sat_top1,
        "combos": {name: {"weights": c["weights"], "result": c["result"]} for name, c in combos.items()},
        "decisions": decisions,
        "all_pass": any(d["all_pass"] for d in decisions.values()),
        "paper_trading_candidate": next((name for name, d in decisions.items() if d["all_pass"]), None),
        "caveats": [
            "Curve-level monthly rebalance ignores intra-month weight drift.",
            "R131 trade-level P&L is not cached; the combined top1/top3/top5 P&L shares are not proven in this round.",
            "The tp20 take-profit fill at the daily high is optimistic.",
            "Overlapping samples and prior use of both candidates mean this is not a fresh out-of-sample test.",
            "Passing would justify a trade-level concentration audit, not deployment.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "core": {k: core_result["full"][k] for k in ("total_return", "mdd", "sharpe")},
        "sat": {k: sat_result["full"][k] for k in ("total_return", "mdd", "sharpe")},
        "combos": {n: {k: c["result"]["full"][k] for k in ("total_return", "mdd", "sharpe")} for n, c in combos.items()},
        "decisions": decisions,
    }, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
