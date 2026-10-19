#!/usr/bin/env python3
"""R197: HS300 new-high/new-low breadth timing strategy backtest (engine)."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r197_breadth_strategy.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R188_CACHE = DATA / "research" / "r197_cache"
STRATEGY = "ai_hs300_breadth_breakout_gate"
WINDOW = 252
SWITCH_COST = 0.0005


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _signal_series() -> pl.DataFrame:
    """Market-level breadth signal; effective next day after month end."""
    paths = sorted((DATA / "ext_data" / "alt_hs300" / "timeseries").glob("date=*/part.parquet"))
    rows = []
    for path in paths:
        day = date.fromisoformat(path.parent.name.split("=")[1])
        frame = pl.read_parquet(path).select("nh_nl_diff_z").drop_nulls()
        if frame.height:
            rows.append({"effective_date": day, "nh_nl_diff_z": float(frame["nh_nl_diff_z"][0])})
    frame = pl.DataFrame(rows).sort("effective_date")
    return frame.with_columns(
        (pl.col("nh_nl_diff_z") > 0).alias("long")
    ).select(["effective_date", "long"])


def _benchmark_curve() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def _window_stats(values: list[float]) -> list[dict]:
    out = []
    for start in range(0, len(values) - WINDOW + 1):
        w = values[start : start + WINDOW]
        peak = w[0]
        mdd = 0.0
        for v in w:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        out.append({"return": w[-1] / w[0] - 1.0, "mdd": mdd})
    return out


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[index]


def _summarize(records: list[dict]) -> dict:
    if not records:
        return {"windows": 0}
    returns = [r["return"] for r in records]
    mdds = [r["mdd"] for r in records]
    return {
        "windows": len(records),
        "positive_rate": round(sum(v > 0 for v in returns) / len(returns), 4),
        "return_p05": round(_quantile(returns, 0.05), 4),
        "return_median": round(st.median(returns), 4),
        "return_worst": round(min(returns), 4),
        "mdd_p05": round(_quantile(mdds, 0.05), 4),
        "mdd_worst": round(min(mdds), 4),
    }


def _compound(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def _mdd(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r114.TARGET_VOL = 0.15
    R188_CACHE.mkdir(parents=True, exist_ok=True)

    membership = r112._historical_membership()
    service = r112._service()
    signals = _signal_series()

    records = []
    strat_stats = {}
    recent = None
    strat_curves = {}
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        path = R188_CACHE / f"{STRATEGY}_{key}_{r112.END.isoformat()}.parquet"
        if path.exists():
            curve = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
        else:
            cfg = r112.StrategyBacktestConfig(
                strategy_id=STRATEGY, symbols=None, start=phase, end=r112.END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                max_positions=300, position_sizing="equal", mode="position",
                asset_type="stock", hs300=True,
            )
            with r112._patched_membership(membership):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{STRATEGY} {phase}: {result.error}")
            curve = r112._curve_frame(result.equity_curve)
            curve.write_parquet(path)
            print(f"ran {STRATEGY} {phase}: ret={result.stats.get('total_return')}", flush=True)
        strat_stats[key] = r112._stats(curve)
        strat_curves[key] = curve
        w = _window_stats(curve["value"].to_list())
        records.extend(w)
        if key == "2025-10-14":
            recent = r112._stats(curve)
        if w:
            print(f"[{key}] ret={strat_stats[key]['total_return']:+.3f} pos="
                  f"{sum(1 for r in w if r['return'] > 0) / len(w):.3f}", flush=True)

    summary = _summarize(records)

    main_key = "2021-06-01"
    main_curve = strat_curves[main_key]
    bench = _benchmark_curve().filter(
        (pl.col("date") >= main_curve["date"][0]) & (pl.col("date") <= main_curve["date"][-1])
    )
    bench_values = bench["close"].to_list()
    bench_returns = [bench_values[i] / bench_values[i - 1] - 1.0 for i in range(1, len(bench_values))]
    bench_total = _compound(bench_returns)
    bench_mdd = _mdd(bench_returns)
    strat_returns = main_curve["value"].to_list()
    strat_returns = [strat_returns[i] / strat_returns[i - 1] - 1.0 for i in range(1, len(strat_returns))]
    strat_total = _compound(strat_returns)
    strat_mdd = _mdd(strat_returns)

    val_curve = main_curve.filter(pl.col("date") >= date(2024, 1, 1))
    val_bench = bench.filter(pl.col("date") >= date(2024, 1, 1))
    val_ret = val_curve["value"].to_list()
    val_ret = [val_ret[i] / val_ret[i - 1] - 1.0 for i in range(1, len(val_ret))]
    vb = val_bench["close"].to_list()
    vb_ret = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    val_total = _compound(val_ret)
    val_bench_total = _compound(vb_ret)

    recent_12 = _compound(strat_returns[-252:]) if len(strat_returns) >= 252 else None
    rolling = _window_stats(main_curve["value"].to_list())
    roll_returns = [r["return"] for r in rolling]

    r131 = json.loads((EVIDENCE / "r131_pit_dual_sleeve.json").read_text(encoding="utf-8"))
    base = r131["candidate_pit_dual_vol15"]
    combo_records = []
    combo_recent = None
    for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
        key = phase.isoformat()
        a = pl.read_parquet(DATA / "research" / "r125_cache" / f"ai_hs300_macro_gate_quality_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
        b = pl.read_parquet(DATA / "research" / "r119_cache" / f"ai_hs300_industry_relative_strength_quality_h20_sl20_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
        risk, _ = r112._combine(a, b, "risk")
        cand = r114._vol_target(risk)["curve"]
        joined = cand.join_asof(
            signals.sort("effective_date"), left_on="date", right_on="effective_date", strategy="backward"
        ).with_columns(pl.col("long").fill_null(False))
        values = joined["value"].to_list()
        longs = joined["long"].to_list()
        combo_values = [1.0]
        position = 1 if longs[0] else 0
        for i in range(1, len(values)):
            ret = values[i] / values[i - 1] - 1.0
            target = 1 if longs[i - 1] else 0
            cost = SWITCH_COST if target != position else 0.0
            position = target
            combo_values.append(combo_values[-1] * (1.0 + target * ret - cost))
        w = _window_stats(combo_values)
        combo_records.extend(w)
        if key == "2025-10-14":
            combo_recent = round(combo_values[-1] - 1.0, 6)
    combo_summary = _summarize(combo_records)

    d1 = len(strat_stats) == 15 and summary["windows"] >= 300
    g1 = strat_total > bench_total
    g2 = summary["positive_rate"] >= 0.75 and summary["return_worst"] >= -0.15
    g3 = recent_12 is not None and recent_12 > 0
    g4 = strat_mdd >= -0.35 and strat_mdd >= bench_mdd
    g5 = val_total > 0 and val_total > val_bench_total
    g6 = (
        combo_summary["windows"] > 0
        and combo_summary["positive_rate"] >= base["positive_rate"] + 0.01
        and combo_summary["return_worst"] >= base["return_worst"] - 0.01
    )
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R197 HS300 new-high/new-low breadth timing strategy",
        "strategy": STRATEGY,
        "strategy_stats": strat_stats,
        "rolling_12m": summary,
        "recent_12m": round(recent_12, 6) if recent_12 is not None else None,
        "main_phase": {
            "window": [str(main_curve["date"][0]), str(main_curve["date"][-1])],
            "strategy_total": round(strat_total, 6),
            "benchmark_total": round(bench_total, 6),
            "strategy_mdd": round(strat_mdd, 6),
            "benchmark_mdd": round(bench_mdd, 6),
            "validation_total": round(val_total, 6),
            "validation_benchmark_total": round(val_bench_total, 6),
        },
        "combo_with_dual_sleeve": combo_summary,
        "combo_recent_12m": combo_recent,
        "reference_r131": base,
        "decision": decision,
        "caveats": [
            "The gate is a market-level monthly signal broadcast to all names; the strategy holds an equal-weight basket of up to 300 names.",
            "The 0.05% per-switch cost is applied at the portfolio level in the combination test.",
            "Only 8 holdout months exist (2026-01..2026-08).",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("main:", json.dumps(result["main_phase"], ensure_ascii=False), flush=True)
    print("rolling:", json.dumps(summary, ensure_ascii=False), flush=True)
    print("recent:", result["recent_12m"], flush=True)
    print("combo:", json.dumps(combo_summary, ensure_ascii=False), flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
