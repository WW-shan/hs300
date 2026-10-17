# 输入说明: R71 — 历史 HS300 多周期低波+价值+低成交额+现金流质量组合平台。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r71_dvq_platform.py
# 结果 JSON: 同目录 r71_dvq_platform.json。
from __future__ import annotations

import importlib.util
import json
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
r70 = _load("r70", "script_r70_lowvol_plateau.py")
FRACTIONS = [0.10, 0.20, 0.30]
MIN_VALID = 60


def _score(reb: pl.DataFrame) -> pl.DataFrame:
    d = reb.with_columns([
        (1.0 - pl.col("vol_60d").rank().over("date") / pl.len().over("date")).alias("_vol60_pct"),
        (1.0 - pl.col("vol_120d").rank().over("date") / pl.len().over("date")).alias("_vol120_pct"),
        (1.0 - pl.col("pb_latest").rank().over("date") / pl.len().over("date")).alias("_pb_pct"),
        (1.0 - pl.col("amount").rank().over("date") / pl.len().over("date")).alias("_amount_pct"),
        (pl.col("ocf_ni").rank().over("date") / pl.len().over("date")).alias("_ocf_pct"),
    ]).with_columns([
        pl.mean_horizontal("_vol60_pct", "_vol120_pct", ignore_nulls=True).alias("_vol_pct"),
        pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int64) for c in ("_vol60_pct", "_vol120_pct", "_pb_pct", "_amount_pct", "_ocf_pct")]).alias("_score_count_raw"),
    ]).with_columns([
        pl.sum_horizontal([
            pl.col("_vol_pct").is_not_null().cast(pl.Int64), pl.col("_pb_pct").is_not_null().cast(pl.Int64),
            pl.col("_amount_pct").is_not_null().cast(pl.Int64), pl.col("_ocf_pct").is_not_null().cast(pl.Int64),
        ]).alias("score_count"),
        pl.mean_horizontal("_vol_pct", "_pb_pct", "_amount_pct", "_ocf_pct", ignore_nulls=True).alias("_score_raw"),
    ]).with_columns(
        pl.when(pl.col("score_count") >= 3).then(pl.col("_score_raw")).otherwise(None).alias("score")
    ).drop(["_vol60_pct", "_vol120_pct", "_pb_pct", "_amount_pct", "_ocf_pct", "_vol_pct", "_score_count_raw", "_score_raw"])
    return d


def _selector(score_col: str, fraction: float):
    def select(d: pl.DataFrame) -> dict[str, float]:
        valid = d.filter(pl.col(score_col).is_not_null() & pl.col("fwd").is_not_null())
        if valid.height < MIN_VALID:
            return {}
        k = max(1, int(valid.height * fraction))
        picked = valid.sort([score_col, "symbol"], descending=[True, False]).head(k)["symbol"].to_list()
        return {symbol: 1.0 / len(picked) for symbol in picked}
    return select


def _baseline(d: pl.DataFrame) -> dict[str, float]:
    valid = d.filter(pl.col("fwd").is_not_null())
    if valid.height < MIN_VALID:
        return {}
    return {symbol: 1.0 / valid.height for symbol in valid["symbol"].to_list()}


def _leg_score(reb: pl.DataFrame, expr, name: str) -> pl.DataFrame:
    return reb.with_columns(expr.alias(name)).select(["symbol", "date", name])


def main() -> int:
    panel, reb, _memberships = r42.build_panel()
    reb = r42._scores(reb)
    reb = r70._vol_panel(panel, reb)
    reb = _score(reb)
    phases = [p.strftime("%Y-%m") for p in r54.r40.r33.phase_dates(r54.r40.PHASE_TARGETS)]
    base, _ = r54._series(reb, _baseline)
    base_m, base_roll = r54._phase(base, phases)
    records = {}
    series_by_fraction = {}
    for fraction in FRACTIONS:
        series, hold = r54._series(reb, _selector("score", fraction))
        metrics, rolling = r54._phase(series, phases)
        excess = r54._excess(series, base)
        hs = r57._hold_stats(hold)
        g1 = bool(metrics["sharpe_mean"] is not None and base_m["sharpe_mean"] is not None and metrics["sharpe_mean"] >= base_m["sharpe_mean"] + 0.10)
        g2 = bool(metrics["annual_mean"] is not None and base_m["annual_mean"] is not None and metrics["annual_mean"] >= base_m["annual_mean"])
        g3 = bool(metrics["mdd_mean"] is not None and base_m["mdd_mean"] is not None and metrics["mdd_mean"] >= base_m["mdd_mean"] - 0.01)
        g4 = bool(metrics["last_year_min"] is not None and base_m["last_year_min"] is not None and metrics["last_year_min"] >= base_m["last_year_min"] and rolling["positive_rate"] >= base_roll["positive_rate"])
        p1 = bool(g1 and g2 and g3 and g4)
        records[f"F{int(fraction * 100)}"] = {"fraction": fraction, "holdings": hs, "metrics": metrics, "rolling": rolling, "excess": excess, "gates": {"G1": g1, "G2": g2, "G3": g3, "G4": g4, "P1": p1}}
        series_by_fraction[int(fraction * 100)] = series
        print("F", fraction, "hold", hs, "sharpe", metrics["sharpe_mean"], "annual", metrics["annual_mean"], "p1", p1, "excess", excess, flush=True)
    sharpe_values = [records[f"F{int(f * 100)}"]["metrics"]["sharpe_mean"] for f in FRACTIONS if records[f"F{int(f * 100)}"]["metrics"]["sharpe_mean"] is not None]
    p2 = bool(len(sharpe_values) == 3 and max(sharpe_values) - min(sharpe_values) <= 0.15)
    primary = records["F20"]
    p3 = bool(
        primary["excess"]["discovery"]["mean"] is not None and primary["excess"]["discovery"]["mean"] > 0
        and primary["excess"]["validation"]["mean"] is not None and primary["excess"]["validation"]["mean"] > 0
        and primary["excess"]["validation"]["t"] is not None and primary["excess"]["validation"]["t"] > 1.5
        and sum(1 for a, b in zip([p["sharpe"] for p in primary["metrics"]["phases"]], [p["sharpe"] for p in base_m["phases"]], strict=False) if a is not None and b is not None and a > b) >= 5
    )
    d1 = bool(all(rec["holdings"]["months"] >= 48 and rec["holdings"]["avg"] is not None and 20 <= rec["holdings"]["avg"] <= 150 for rec in records.values()) and r70._coverage(reb, "score")["mean"] is not None and r70._coverage(reb, "score")["mean"] >= 0.70)
    decision = {"D1": d1, "P1_count": sum(1 for rec in records.values() if rec["gates"]["P1"]), "P2": p2, "P3": p3, "all_pass": bool(d1 and all(rec["gates"]["P1"] for rec in records.values()) and p2 and p3)}
    # Single-leg correlations vs the 20% composite, report-only.
    # Build leg percentiles directly and use the same 20% selector.
    legs = reb.with_columns([
        pl.mean_horizontal(1.0 - pl.col("vol_60d").rank().over("date") / pl.len().over("date"), 1.0 - pl.col("vol_120d").rank().over("date") / pl.len().over("date"), ignore_nulls=True).alias("leg_vol"),
        (1.0 - pl.col("pb_latest").rank().over("date") / pl.len().over("date")).alias("leg_pb"),
        (1.0 - pl.col("amount").rank().over("date") / pl.len().over("date")).alias("leg_amount"),
        (pl.col("ocf_ni").rank().over("date") / pl.len().over("date")).alias("leg_ocf"),
    ])
    leg_corr = {}
    for leg in ("leg_vol", "leg_pb", "leg_amount", "leg_ocf"):
        leg_series, _ = r54._series(legs, _selector(leg, 0.20))
        leg_corr[leg] = r54._corr(series_by_fraction[20], leg_series)
    result = {
        "post_hoc": True,
        "window": {"phases": phases, "fractions": FRACTIONS, "components": ["vol_60d", "vol_120d", "pb_latest", "amount", "ocf_ni"]},
        "baseline": {"metrics": base_m, "rolling": base_roll},
        "combinations": records,
        "score_coverage": r70._coverage(reb, "score"),
        "single_leg_corr_f20": leg_corr,
        "decision": decision,
        "passed": ["DVQ_PLATFORM"] if decision["all_pass"] else [],
        "recommendation": "R72 risk/cost final" if decision["all_pass"] else "negative knowledge; composite line closed",
        "caveats": ["事后组合, 不是独立样本外", "至少 3/4 维度有效", "vol uses adjusted close and current float universe", "historical membership monthly snapshots"],
    }
    path = EV / "r71_dvq_platform.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("decision", decision, "corr", leg_corr, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
