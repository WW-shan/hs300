# 输入说明: R52 — 纳入后 60 日 + 两融高增长 top20% 双风险过滤并集。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r52_union_overlay.py
# 结果 JSON: 同目录 r52_union_overlay.json。
from __future__ import annotations

import bisect
import importlib.util
import json
import statistics as st
from pathlib import Path

import numpy as np
import polars as pl

EV = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EV / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


r42 = _load("r42", "script_r42_historical_factor_screen.py")
r46 = _load("r46", "script_r46_index_inclusion.py")
r49 = _load("r49", "script_r49_index_overlay.py")
r50 = _load("r50", "script_r50_margin.py")
r51 = _load("r51", "script_r51_margin_overlay.py")
r40 = r50.r40
r30 = r49.r30
EXCLUDE_DAYS = 60
SEED = 20261017
DRAWS = 500


def _inclusion_flags(reb: pl.DataFrame, events: pl.DataFrame, trading_dates: list) -> pl.DataFrame:
    event_idx = {}
    for keys, group in events.group_by("symbol"):
        symbol = keys[0] if isinstance(keys, tuple) else keys
        event_idx[symbol] = sorted(bisect.bisect_left(trading_dates, d) for d in group["effective_date"].to_list())
    flags = []
    for row in reb.iter_rows(named=True):
        idx = bisect.bisect_left(trading_dates, row["date"])
        last = max((i for i in event_idx.get(row["symbol"], []) if i <= idx), default=-10**9)
        flags.append(idx - last < EXCLUDE_DAYS)
    return reb.with_columns(pl.Series("inclusion_flag", flags))


def _metrics(frame: pl.DataFrame, phases: list[str]) -> tuple[dict, dict]:
    frames = [frame.filter(pl.col("ym") >= p) for p in phases]
    return r30.phase_metrics(frames), r30.rolling_windows(frames)


def _month_counts(reb: pl.DataFrame, col: str) -> pl.DataFrame:
    return reb.filter(pl.col("fwd").is_not_null()).group_by("date").agg([
        pl.col(col).sum().alias("excluded"),
        pl.len().alias("eligible"),
    ]).sort("date")


def main() -> int:
    panel, reb, _ = r42.build_panel()
    reb = r42._scores(reb)
    events, event_audit = r46._events()
    memberships, _ = r40.membership_frame()
    symbols = sorted(memberships["symbol"].unique().to_list())
    combined, _ = r40.load_combined_prices(symbols)
    _, _, trading_dates = r49.r43._series_and_bench(combined, memberships)
    snaps = r50._snapshots(panel)
    reb = r50._attach(reb, snaps)
    reb = _inclusion_flags(reb, events, trading_dates)
    reb, _margin_counts, margin_audit = r51._exclude_flags(reb)
    reb = reb.rename({"excluded_flag": "margin_flag"}).with_columns(
        (pl.col("inclusion_flag") | pl.col("margin_flag")).alias("union_flag")
    )
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]
    base = r49._portfolio_series(reb, "")
    inc = r49._portfolio_series(reb, "inclusion_flag")
    mar = r49._portfolio_series(reb, "margin_flag")
    union = r49._portfolio_series(reb, "union_flag")
    base_metrics, base_roll = _metrics(base, phases)
    inc_metrics, inc_roll = _metrics(inc, phases)
    mar_metrics, mar_roll = _metrics(mar, phases)
    union_metrics, union_roll = _metrics(union, phases)
    count_a = _month_counts(reb, "inclusion_flag")
    count_b = _month_counts(reb, "margin_flag")
    count_u = _month_counts(reb, "union_flag")
    overlaps = []
    for day in count_u["date"].to_list():
        a_symbols = set(reb.filter((pl.col("date") == day) & pl.col("inclusion_flag"))["symbol"].to_list())
        b_symbols = set(reb.filter((pl.col("date") == day) & pl.col("margin_flag"))["symbol"].to_list())
        overlaps.append({
            "intersection": len(a_symbols & b_symbols),
            "union": len(a_symbols | b_symbols),
        })
    data = {
        "event_audit": event_audit,
        "margin_audit": margin_audit,
        "months": count_u.height,
        "months_with_union": int((count_u["excluded"] > 0).sum()),
        "avg_excluded": round(float(count_u["excluded"].mean()), 2),
        "max_excluded": int(count_u["excluded"].max()),
        "avg_inclusion": round(float(count_a["excluded"].mean()), 2),
        "avg_margin": round(float(count_b["excluded"].mean()), 2),
        "avg_intersection": round(float(st.mean(row["intersection"] for row in overlaps)), 2),
        "avg_union": round(float(st.mean(row["union"] for row in overlaps)), 2),
    }
    actual_sharpe = union_metrics["sharpe_mean"]
    count_map = dict(zip(count_u["date"].to_list(), count_u["excluded"].to_list(), strict=False))
    placebo = [r51._mean_sharpe(r51._random_rows(reb, count_map, SEED + i), phases) for i in range(DRAWS)]
    placebo = [v for v in placebo if v is not None]
    p95 = float(np.percentile(placebo, 95)) if placebo else None
    union_sharpes = [p["sharpe"] for p in union_metrics["phases"]]
    base_sharpes = [p["sharpe"] for p in base_metrics["phases"]]
    d1 = bool(
        data["months_with_union"] >= 24
        and 20 <= data["avg_excluded"] <= 120
        and data["max_excluded"] <= 120
        and data["months"] >= 48
    )
    g1 = bool(union_metrics["sharpe_mean"] >= base_metrics["sharpe_mean"] + 0.10)
    g2 = bool(union_metrics["annual_mean"] >= base_metrics["annual_mean"])
    g3 = bool(union_metrics["mdd_mean"] >= base_metrics["mdd_mean"] - 0.01)
    g4 = bool(union_metrics["last_year_min"] >= base_metrics["last_year_min"] and union_roll["positive_rate"] >= base_roll["positive_rate"])
    g5 = bool(sum(1 for x, y in zip(union_sharpes, base_sharpes, strict=False) if x is not None and y is not None and x > y) >= 5)
    g6 = bool(actual_sharpe >= max(inc_metrics["sharpe_mean"], mar_metrics["sharpe_mean"]) + 0.02)
    g7 = bool(actual_sharpe > p95) if p95 is not None else False
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7, "all_pass": bool(d1 and g1 and g2 and g3 and g4 and g5 and g6 and g7)}
    result = {
        "post_hoc": True,
        "rule": {"inclusion_exclude_days": EXCLUDE_DAYS, "margin_top_fraction": 0.20, "placebo_draws": DRAWS, "seed": SEED},
        "data": data,
        "base": {"metrics": base_metrics, "rolling": base_roll},
        "inclusion": {"metrics": inc_metrics, "rolling": inc_roll},
        "margin": {"metrics": mar_metrics, "rolling": mar_roll},
        "union": {"metrics": union_metrics, "rolling": union_roll},
        "placebo": {"mean_sharpe": round(st.mean(placebo), 4) if placebo else None, "p95": round(p95, 4) if p95 is not None else None, "actual": actual_sharpe, "n": len(placebo)},
        "phase_sharpe": {"base": base_sharpes, "inclusion": [p["sharpe"] for p in inc_metrics["phases"]], "margin": [p["sharpe"] for p in mar_metrics["phases"]], "union": union_sharpes},
        "decision": decision,
        "recommendation": "risk-layer integration" if decision["all_pass"] else "negative knowledge; risk-filter line closed",
        "caveats": ["事后假设", "两融只覆盖可融资融券标的", "纳入识别依赖月度快照", "未证明对 B80/C6 的增量", "随机 placebo 使用相同排除数量"],
    }
    path = EV / "r52_union_overlay.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("data", data, "base/inc/margin/union", base_metrics["sharpe_mean"], inc_metrics["sharpe_mean"], mar_metrics["sharpe_mean"], union_metrics["sharpe_mean"], flush=True)
    print("placebo", result["placebo"], "decision", decision, "saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
