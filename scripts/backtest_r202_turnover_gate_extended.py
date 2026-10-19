#!/usr/bin/env python3
"""R202: validate the R201 turnover gate on the independent 2018-2020 sample."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r202_turnover_gate_extended.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
CACHE = DATA / "research" / "r163_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2018, 1, 2)
END = date(2026, 10, 16)
INDEP_START = date(2018, 2, 1)
INDEP_END = date(2020, 12, 31)
WINDOW = 252
SWITCH_COST = 0.0005


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _compound(values: list[float]) -> float:
    total = 1.0
    for v in values:
        total *= 1.0 + v
    return total - 1.0


def _mdd(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for v in values:
        equity *= 1.0 + v
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _window_stats(values: list[float]) -> list[float]:
    if len(values) < WINDOW:
        return []
    return [_compound(values[i : i + WINDOW]) for i in range(0, len(values) - WINDOW + 1)]


def _segment_stats(values: list[float]) -> dict:
    if not values:
        return {"days": 0}
    windows = _window_stats(values)
    return {
        "days": len(values),
        "total_return": round(_compound(values), 6),
        "mdd": round(_mdd(values), 6),
        "rolling_1y_windows": len(windows),
        "rolling_1y_positive_rate": round(sum(v > 0 for v in windows) / len(windows), 4) if windows else None,
        "rolling_1y_median": round(st.median(windows), 4) if windows else None,
        "rolling_1y_worst": round(min(windows), 4) if windows else None,
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15

    a = pl.read_parquet(CACHE / f"{A_SLEEVE}_{START.isoformat()}_{END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
    b = pl.read_parquet(CACHE / f"{B_SLEEVE}_{START.isoformat()}_{END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
    risk, _ = r112._combine(a, b, "risk")
    candidate = r114._vol_target(risk)["curve"].sort("date")

    scan = json.loads((EVIDENCE / "r201_turnover_scan.json").read_text(encoding="utf-8"))
    records = scan["monthly_records"]
    signal = pl.DataFrame([
        {"effective_date": date.fromisoformat(r["date"]), "hs300_turnover_z": r["hs300_turnover_z"]}
        for r in records
        if r.get("hs300_turnover_z") is not None
    ]).with_columns(pl.col("effective_date") + pl.duration(days=1)).sort("effective_date")

    joined = candidate.join_asof(signal, left_on="date", right_on="effective_date", strategy="backward")
    values = joined["value"].to_list()
    zs = joined["hs300_turnover_z"].to_list()

    ungated = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    gated = []
    position = 1  # candidate starts fully invested; no gate during warmup
    switches = 0
    for i in range(1, len(values)):
        ret = values[i] / values[i - 1] - 1.0
        z = zs[i]
        target = 1 if z is None or z < 0 else 0
        cost = SWITCH_COST if target != position else 0.0
        switches += int(target != position)
        position = target
        gated.append(target * ret - cost)

    dates = joined["date"].to_list()[1:]

    def seg(values_, start, end):
        return [v for d, v in zip(dates, values_) if start <= d <= end]

    indep_u = seg(ungated, INDEP_START, INDEP_END)
    indep_g = seg(gated, INDEP_START, INDEP_END)
    full_u = ungated
    full_g = gated

    # signal coverage
    indep_days = [d for d in dates if INDEP_START <= d <= INDEP_END]
    indep_signal = [z for d, z in zip(dates, zs[1:]) if INDEP_START <= d <= INDEP_END]
    coverage_indep = sum(1 for z in indep_signal if z is not None) / len(indep_signal) if indep_signal else 0.0
    coverage_full = sum(1 for z in zs[1:] if z is not None) / len(zs[1:]) if zs else 0.0

    su = _segment_stats(indep_u)
    sg = _segment_stats(indep_g)
    fu = _segment_stats(full_u)
    fg = _segment_stats(full_g)

    d1 = coverage_indep >= 0.50 and coverage_full >= 0.90
    g1 = sg["total_return"] is not None and su["total_return"] is not None and sg["total_return"] >= su["total_return"] + 0.05
    g2 = sg["mdd"] is not None and su["mdd"] is not None and sg["mdd"] >= su["mdd"] - 0.02
    g3 = (
        fg["rolling_1y_positive_rate"] is not None and fu["rolling_1y_positive_rate"] is not None
        and fg["rolling_1y_positive_rate"] >= fu["rolling_1y_positive_rate"]
    )
    g4 = fg["mdd"] is not None and fu["mdd"] is not None and fg["mdd"] >= fu["mdd"] - 0.02
    g5 = fg["total_return"] is not None and fu["total_return"] is not None and fg["total_return"] >= fu["total_return"] - 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R202 turnover gate independent-sample validation (2018-2020)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "candidate_source": "r163_cache A/B sleeves, 3x cost, 15% vol target",
        "signal": "R201 hs300_turnover_z < 0 -> long; null -> no gate",
        "switch_cost": SWITCH_COST,
        "coverage": {"indep_2018_2020": round(coverage_indep, 4), "full_2018_2026": round(coverage_full, 4)},
        "independent_2018_2020": {"ungated": su, "gated": sg, "switches": switches},
        "full_2018_2026": {"ungated": fu, "gated": fg},
        "decision": decision,
        "caveats": [
            "2018-2020 has only ~35 months; the z-score warmup leaves ~10 months ungated.",
            "The gate was discovered on 2021-2026; this is the first independent test.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
