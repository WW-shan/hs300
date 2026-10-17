# 输入说明: R51 — 高融资余额增长 top20% 排除风险层。进程内运行:
#   cd backend && uv run python ../data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/script_r51_margin_overlay.py
# 结果 JSON: 同目录 r51_margin_overlay.json。
from __future__ import annotations

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
r50 = _load("r50", "script_r50_margin.py")
r49 = _load("r49", "script_r49_index_overlay.py")
r40 = r50.r40
r30 = r49.r30
COST_2X = 0.0028
TOP_FRACTION = 0.20
MIN_VALID = 60
PLACEBO_DRAWS = 500
SEED = 20261017


def _exclude_flags(reb: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    counts = []
    flags = []
    for day in sorted(reb["date"].unique().to_list()):
        d = reb.filter((pl.col("date") == day) & pl.col("margin_chg_1m").is_not_null())
        n = d.height
        k = max(1, int(n * TOP_FRACTION)) if n >= MIN_VALID else 0
        if k:
            ranked = d.with_columns(
                pl.col("margin_chg_1m").rank(method="ordinal", descending=True).alias("_rank")
            )
            excluded = set(ranked.filter(pl.col("_rank") <= k)["symbol"].to_list())
        else:
            excluded = set()
        counts.append({"date": day, "valid": n, "excluded": len(excluded)})
        flags.extend({"date": day, "symbol": symbol, "excluded_flag": symbol in excluded} for symbol in reb.filter(pl.col("date") == day)["symbol"].to_list())
    flag_frame = pl.DataFrame(flags).with_columns(pl.col("date").cast(pl.Date)).with_columns(pl.col("excluded_flag").cast(pl.Boolean))
    count_frame = pl.DataFrame(counts).with_columns(pl.col("date").cast(pl.Date))
    audit = {
        "months": count_frame.height,
        "months_valid": int((count_frame["valid"] >= MIN_VALID).sum()),
        "months_with_exclusion": int((count_frame["excluded"] > 0).sum()),
        "avg_excluded": round(float(count_frame["excluded"].mean()), 2),
        "max_excluded": int(count_frame["excluded"].max()),
    }
    return reb.join(flag_frame, on=["symbol", "date"], how="left").with_columns(pl.col("excluded_flag").fill_null(False)), count_frame, audit


def _mean_sharpe(rows: list[dict], phases: list[str]) -> float | None:
    vals = []
    for start in phases:
        r = [float(row["r"]) for row in rows if row["ym"] >= start]
        if len(r) >= 6:
            sd = st.pstdev(r)
            if sd > 0:
                vals.append(st.mean(r) / sd * (12 ** 0.5))
    return round(st.mean(vals), 4) if vals else None


def _random_rows(reb: pl.DataFrame, count_map: dict, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    dates = sorted(reb["date"].unique().to_list())
    prev = {}
    rows = []
    for day in dates:
        d = reb.filter((pl.col("date") == day) & pl.col("fwd").is_not_null())
        symbols = d["symbol"].to_list()
        if not symbols:
            continue
        k = min(int(count_map.get(day, 0)), len(symbols))
        if k:
            drop = set(rng.choice(np.array(symbols, dtype=object), size=k, replace=False).tolist())
            keep = [s for s in symbols if s not in drop]
        else:
            keep = symbols
        if not keep:
            continue
        weights = {s: 1.0 / len(keep) for s in keep}
        turn = 1.0 if not prev else 0.5 * sum(abs(weights.get(s, 0.0) - prev.get(s, 0.0)) for s in set(weights) | set(prev))
        gross = float(d.filter(pl.col("symbol").is_in(keep))["fwd"].mean())
        rows.append({"ym": day.strftime("%Y-%m"), "r": gross - COST_2X * turn})
        prev = weights
    return rows


def main() -> int:
    panel, reb, _ = r42.build_panel()
    reb = r42._scores(reb)
    snaps = r50._snapshots(panel)
    reb = r50._attach(reb, snaps)
    reb, counts, audit = _exclude_flags(reb)
    base = r49._portfolio_series(reb, "")
    over = r49._portfolio_series(reb, "excluded_flag")
    phases = [p.strftime("%Y-%m") for p in r40.r33.phase_dates(r40.PHASE_TARGETS)]
    base_frames = [base.filter(pl.col("ym") >= p) for p in phases]
    over_frames = [over.filter(pl.col("ym") >= p) for p in phases]
    base_metrics = r30.phase_metrics(base_frames)
    over_metrics = r30.phase_metrics(over_frames)
    base_roll = r30.rolling_windows(base_frames)
    over_roll = r30.rolling_windows(over_frames)
    base_sharpes = [p["sharpe"] for p in base_metrics["phases"]]
    over_sharpes = [p["sharpe"] for p in over_metrics["phases"]]
    actual_mean_sharpe = _mean_sharpe(over.to_dicts(), phases)
    count_map = dict(zip(counts["date"].to_list(), counts["excluded"].to_list(), strict=False))
    placebo = [_mean_sharpe(_random_rows(reb, count_map, SEED + i), phases) for i in range(PLACEBO_DRAWS)]
    placebo = [v for v in placebo if v is not None]
    p95 = float(np.percentile(placebo, 95)) if placebo else None
    d1 = bool(
        audit["months_valid"] >= 48
        and audit["months_with_exclusion"] >= 24
        and 20 <= audit["avg_excluded"] <= 80
        and audit["max_excluded"] <= 80
    )
    g1 = bool(over_metrics["sharpe_mean"] >= base_metrics["sharpe_mean"] + 0.10)
    g2 = bool(over_metrics["annual_mean"] >= base_metrics["annual_mean"])
    g3 = bool(over_metrics["mdd_mean"] >= base_metrics["mdd_mean"] - 0.01)
    g4 = bool(over_metrics["last_year_min"] >= base_metrics["last_year_min"] and over_roll["positive_rate"] >= base_roll["positive_rate"])
    g5 = bool(sum(1 for a, b in zip(over_sharpes, base_sharpes, strict=False) if a is not None and b is not None and a > b) >= 5)
    g6 = bool(actual_mean_sharpe is not None and p95 is not None and actual_mean_sharpe > p95)
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "all_pass": bool(d1 and g1 and g2 and g3 and g4 and g5 and g6)}
    result = {
        "post_hoc": True,
        "rule": {"top_fraction": TOP_FRACTION, "min_valid": MIN_VALID, "cost_2x": COST_2X, "placebo_draws": PLACEBO_DRAWS, "seed": SEED},
        "data": audit,
        "base": {"metrics": base_metrics, "rolling": base_roll},
        "overlay": {"metrics": over_metrics, "rolling": over_roll},
        "phase_sharpe": {"base": base_sharpes, "overlay": over_sharpes, "overlay_better": sum(1 for a, b in zip(over_sharpes, base_sharpes, strict=False) if a is not None and b is not None and a > b)},
        "placebo": {"mean_sharpe": round(st.mean(placebo), 4) if placebo else None, "p95": round(p95, 4) if p95 is not None else None, "actual": actual_mean_sharpe, "n": len(placebo)},
        "decision": decision,
        "recommendation": "R52 risk-layer integration" if decision["all_pass"] else "negative knowledge; do not deploy",
        "caveats": ["事后假设, 非独立发现", "两融只覆盖可融资融券标的", "随机 placebo 使用相同排除数量但未做行业匹配", "未证明对 B80/C6 的增量"],
    }
    path = EV / "r51_margin_overlay.json"
    path.write_text(json.dumps(r42.r22.compact(result), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("audit", audit, "base", base_metrics["sharpe_mean"], "over", over_metrics["sharpe_mean"], flush=True)
    print("placebo", result["placebo"], "decision", decision, flush=True)
    print("saved", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
