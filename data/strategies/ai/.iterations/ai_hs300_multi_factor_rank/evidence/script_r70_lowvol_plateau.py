# 输入说明: R70 — 历史 HS300 低波动窗口 × 持仓分位参数平台检验。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r70_lowvol_plateau.py
# 结果 JSON: 同目录 r70_lowvol_plateau.json。
from __future__ import annotations

import importlib.util
import json
import math
from itertools import pairwise
from pathlib import Path

import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r54 = _load("r54", "script_r54_long_only.py")
r57 = _load("r57", "script_r57_composites.py")
WINDOWS = [10, 20, 60, 120]
FRACTIONS = [0.10, 0.20, 0.30]
MIN_VALID = 60
MAX_HOLDINGS = 150


def _vol_panel(panel: pl.DataFrame, reb: pl.DataFrame) -> pl.DataFrame:
    px = panel.select(["symbol", "date", "close"]).sort(["symbol", "date"])
    px = px.with_columns((pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("_ret"))
    vols = px.select(["symbol", "date", "_ret"])
    for window in WINDOWS:
        vols = vols.with_columns(
            (pl.col("_ret").rolling_std(window_size=window, min_samples=window).over("symbol") * math.sqrt(252.0)).alias(f"vol_{window}d")
        )
    return reb.join(vols.drop("_ret"), on=["symbol", "date"], how="left")


def _selector(col: str, fraction: float):
    def select(d: pl.DataFrame) -> dict[str, float]:
        valid = d.filter(pl.col(col).is_not_null() & pl.col("fwd").is_not_null())
        if valid.height < MIN_VALID:
            return {}
        k = max(1, int(valid.height * fraction))
        picked = valid.sort([col, "symbol"]).head(k)["symbol"].to_list()
        return {symbol: 1.0 / len(picked) for symbol in picked}
    return select


def _baseline(d: pl.DataFrame) -> dict[str, float]:
    valid = d.filter(pl.col("fwd").is_not_null())
    if valid.height < MIN_VALID:
        return {}
    return {symbol: 1.0 / valid.height for symbol in valid["symbol"].to_list()}


def _coverage(reb: pl.DataFrame, col: str) -> dict:
    grouped = reb.group_by("date").agg([pl.len().alias("eligible"), pl.col(col).is_not_null().sum().alias("valid")])
    if grouped.is_empty():
        return {"mean": None, "min_valid": 0, "months": 0}
    return {"mean": round(float((grouped["valid"] / grouped["eligible"]).mean()), 6), "min_valid": int(grouped["valid"].min()), "months": grouped.height}


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    reb = _vol_panel(panel, reb)
    phases = [p.strftime("%Y-%m") for p in r54.r40.r33.phase_dates(r54.r40.PHASE_TARGETS)]
    base, _ = r54._series(reb, _baseline)
    base_m, base_roll = r54._phase(base, phases)
    print("base", base_m["sharpe_mean"], base_m["annual_mean"], "reb", reb.shape, flush=True)

    records = {}
    for window in WINDOWS:
        col = f"vol_{window}d"
        coverage = _coverage(reb, col)
        for fraction in FRACTIONS:
            key = f"W{window}_F{int(fraction * 100)}"
            series, hold = r54._series(reb, _selector(col, fraction))
            metrics, rolling = r54._phase(series, phases)
            excess = r54._excess(series, base)
            hs = r57._hold_stats(hold)
            g1 = bool(metrics["sharpe_mean"] is not None and base_m["sharpe_mean"] is not None and metrics["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10)
            g2 = bool(metrics["annual_mean"] is not None and base_m["annual_mean"] is not None and metrics["annual_mean"] >= base_m["annual_mean"])
            g3 = bool(metrics["mdd_mean"] is not None and base_m["mdd_mean"] is not None and metrics["mdd_mean"] >= base_m["mdd_mean"] - 0.01)
            g4 = bool(metrics["last_year_min"] is not None and base_m["last_year_min"] is not None and metrics["last_year_min"] >= base_m["last_year_min"] and rolling["positive_rate"] >= base_roll["positive_rate"])
            d1 = bool(hs["months"] >= 48 and hs["avg"] is not None and 20 <= hs["avg"] <= MAX_HOLDINGS and coverage["mean"] is not None and coverage["mean"] >= 0.70)
            p1 = bool(g1 and g2 and g3 and g4)
            records[key] = {
                "window": window, "fraction": fraction, "coverage": coverage, "holdings": hs,
                "metrics": metrics, "rolling": rolling, "excess": excess,
                "gates": {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "P1": p1},
            }
            print(key, "hold", hs, "sharpe", metrics["sharpe_mean"], "annual", metrics["annual_mean"], "p1", p1, "valExcess", excess["validation"], flush=True)

    p1_keys = [key for key, rec in records.items() if rec["gates"]["P1"]]
    p2 = False
    for fraction in (0.20, 0.30):
        keys = [f"W{window}_F{int(fraction * 100)}" for window in WINDOWS]
        for left, right in pairwise(keys):
            if left in p1_keys and right in p1_keys:
                p2 = True
                break
        if p2:
            break
    valid_positive = sum(1 for rec in records.values() if rec["excess"]["validation"]["mean"] is not None and rec["excess"]["validation"]["mean"] > 0)
    val_ts = [rec["excess"]["validation"]["t"] for rec in records.values() if rec["excess"]["validation"]["t"] is not None]
    median_val_t = float(statistics_median(val_ts)) if val_ts else None
    p3 = bool(valid_positive >= 9 and median_val_t is not None and median_val_t > 1.5)

    best = None
    if p1_keys:
        best = max(p1_keys, key=lambda key: records[key]["excess"]["validation"]["t"] if records[key]["excess"]["validation"]["t"] is not None else -9)
    neighbor_diffs = {}
    p4 = False
    if best:
        rec = records[best]
        window_idx = WINDOWS.index(rec["window"])
        frac_idx = FRACTIONS.index(rec["fraction"])
        candidates = []
        if window_idx > 0:
            candidates.append(f"W{WINDOWS[window_idx - 1]}_F{int(rec['fraction'] * 100)}")
        if window_idx + 1 < len(WINDOWS):
            candidates.append(f"W{WINDOWS[window_idx + 1]}_F{int(rec['fraction'] * 100)}")
        if frac_idx > 0:
            candidates.append(f"W{rec['window']}_F{int(FRACTIONS[frac_idx - 1] * 100)}")
        if frac_idx + 1 < len(FRACTIONS):
            candidates.append(f"W{rec['window']}_F{int(FRACTIONS[frac_idx + 1] * 100)}")
        for key in candidates:
            other = records[key]
            diff = abs((rec["metrics"]["sharpe_mean"] or -9) - (other["metrics"]["sharpe_mean"] or -9))
            neighbor_diffs[key] = round(diff, 4)
            if diff <= 0.10:
                p4 = True
    decision = {"P1_count": len(p1_keys), "P1_required": 9, "P2": p2, "P3": p3, "P4": p4, "valid_positive": valid_positive, "median_validation_t": median_val_t, "best": best, "neighbor_diffs": neighbor_diffs}
    decision["all_pass"] = bool(len(p1_keys) >= 9 and p2 and p3 and p4)
    result = {
        "window": {"start": "2021-06", "end": "2026-10", "phases": phases, "windows": WINDOWS, "fractions": FRACTIONS},
        "baseline": {"metrics": base_m, "rolling": base_roll},
        "combinations": records,
        "decision": decision,
        "passed": ["LOWVOL_PLATFORM"] if decision["all_pass"] else [],
        "recommendation": "R71 combo/risk integration" if decision["all_pass"] else "negative knowledge; parameter optimization line closed",
        "caveats": ["vol uses adjusted close and current float universe", "historical membership is monthly snapshots", "2x cost applies to all rebalances", "parameter grid fixed before run"],
    }
    path = EV / "r70_lowvol_plateau.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", decision, "saved", path, flush=True)
    return 0


def statistics_median(values):
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0


if __name__ == "__main__":
    raise SystemExit(main())
