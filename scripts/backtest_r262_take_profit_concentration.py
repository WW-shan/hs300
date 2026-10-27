#!/usr/bin/env python3
"""R262: R257 concentration fix via fixed take-profit or shorter holding."""

from __future__ import annotations

import gc
import importlib.util
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r262_take_profit_concentration.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R221_SCRIPT = ROOT / "scripts" / "backtest_r221_sga_nonfin_quality.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R257_SCRIPT = ROOT / "scripts" / "backtest_r257_quality_relative_strength.py"
CACHE = DATA / "research" / "r262_cache"
R257_CACHE = DATA / "research" / "r257_cache"
ROUND = "R262"
START = date(2023, 5, 4)
END = date(2026, 10, 16)
SEGMENTS = [(date(2023, 5, 4), date(2024, 12, 31)), (date(2025, 1, 2), END)]
SID = "ai_hs300_quality_relative_strength_h20_sl20"
VARIANTS = {
    "tp20": {"take_profit": 0.20, "max_hold_days": 20},
    "tp30": {"take_profit": 0.30, "max_hold_days": 20},
    "tp40": {"take_profit": 0.40, "max_hold_days": 20},
    "h10": {"take_profit": None, "max_hold_days": 10},
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_variant(service, membership, r112, r229, variant: str, start: date, end: date, cost: float):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{variant}_{cost:g}x_{start.isoformat()}_{end.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and curve_path.stat().st_size and trades_path.exists() and trades_path.stat().st_size:
        frame = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return frame, {"cached": True, "days": frame.height, "curve_stats": r112._stats(frame)}, trades

    spec = VARIANTS[variant]
    scale = cost / 3.0
    cfg = r112.StrategyBacktestConfig(
        strategy_id=SID,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides={"take_profit": spec["take_profit"], "max_hold_days": spec["max_hold_days"]},
        matching="open_t+1",
        fees_pct=0.0006 * scale,
        commission_pct=0.0006 * scale,
        stamp_tax_pct=0.001 * scale,
        slippage_bps=15.0 * scale,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        if "未产生买入信号" in str(result.error):
            frame = pl.DataFrame({"date": r229._benchmark_dates(start, end)}).with_columns(
                pl.lit(1_000_000.0).alias("value")
            )
            trades = []
        else:
            raise RuntimeError(f"{SID} {variant} {cost:g}x {start}~{end}: {result.error}")
    else:
        frame = r112._curve_frame(result.equity_curve)
        trades = [
            {
                "segment": f"{start.isoformat()}~{end.isoformat()}",
                "symbol": trade.get("symbol"),
                "name": trade.get("name"),
                "entry_date": trade.get("entry_date"),
                "exit_date": trade.get("exit_date"),
                "pnl_pct": trade.get("pnl_pct"),
                "pnl_amount": trade.get("pnl_amount"),
                "exit_reason": trade.get("exit_reason"),
            }
            for trade in result.trades
        ]
    frame.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return frame, {
        "cached": False,
        "days": frame.height,
        "curve_stats": r112._stats(frame),
        "result_stats": result.stats,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }, trades


def _segmented(service, membership, r112, r229, variant: str, cost: float):
    curves = []
    runs = []
    trades = []
    for start, end in SEGMENTS:
        curve, info, segment_trades = _run_variant(service, membership, r112, r229, variant, start, end, cost)
        curves.append(curve)
        runs.append({"start": str(start), "end": str(end), **info})
        trades.extend(segment_trades)
        print(f"{variant} {cost:g}x {start}~{end}: {info['curve_stats']}", flush=True)
        gc.collect()
    stitched = r229._stitch(curves)
    result = r229._result(r112, stitched)
    result["discovery_2023_2024"] = result.pop("discovery_2022_2024")
    return {"runs": runs, "result": result}, trades


def _load_control(r112, r229):
    curves = []
    trades = []
    for start, end in SEGMENTS:
        key = f"{SID}_3x_{start.isoformat()}_{end.isoformat()}"
        curve_path = R257_CACHE / f"{key}.parquet"
        trades_path = R257_CACHE / f"{key}.trades.json"
        frame = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        curves.append(frame)
        trades.extend(json.loads(trades_path.read_text(encoding="utf-8")))
    stitched = r229._stitch(curves)
    result = r229._result(r112, stitched)
    result["discovery_2023_2024"] = result.pop("discovery_2022_2024")
    return result, trades


def _concentration(r239, trades, industry):
    full = r239._aggregate(trades, industry)
    validation = [t for t in trades if t["segment"].startswith("2025-01-02~")]
    val = r239._aggregate(validation, industry)
    positive = sum(1 for row in val["per_symbol"] if row["pnl_amount"] > 0)
    return full, val, positive


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r262")
    r221 = _load(R221_SCRIPT, "r221_r262")
    r229 = _load(R229_SCRIPT, "r229_r262")
    r239 = _load(R239_SCRIPT, "r239_r262")

    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    service = r112._service()
    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }

    control_result, control_trades = _load_control(r112, r229)

    results = {}
    all_trades = {}
    concentrations = {}
    for variant in VARIANTS:
        result, trades = _segmented(service, membership, r112, r229, variant, 3.0)
        results[variant] = result
        all_trades[variant] = trades
        full, val, positive = _concentration(r239, trades, industry)
        concentrations[variant] = {"full": full, "validation": val, "validation_positive_symbols": positive}

    control_full, _, _ = _concentration(r239, control_trades, industry)

    decisions = {}
    for variant in VARIANTS:
        result = results[variant]["result"]
        conc = concentrations[variant]
        full = result["full"]
        conc_full = conc["full"]
        g1 = result["discovery_2023_2024"]["total_return"] >= control_result["discovery_2023_2024"]["total_return"] - 0.10
        g2 = result["validation_2025_2026"]["total_return"] > 0
        g3 = full["total_return"] > 0
        g4 = full["total_return"] >= control_result["full"]["total_return"] * 0.60
        g5 = full["mdd"] >= -0.40
        g6 = (result["rolling_1y"]["positive_rate"] or 0.0) >= 0.60
        g7 = (result["recent_12m"]["total_return"] or 0.0) > 0
        c1 = conc_full["distinct_symbols"] >= 30
        c2 = conc_full["total_pnl_amount"] > 0 and conc_full["top1_net_share"] <= 0.25
        c3 = conc_full["total_pnl_amount"] > 0 and conc_full["top3_net_share"] <= 0.55
        c4 = conc_full["total_pnl_amount"] > 0 and conc_full["top5_net_share"] <= 0.75
        c5 = conc["validation_positive_symbols"] >= 8
        c6 = conc_full["top_industry_gross_share"] is not None and conc_full["top_industry_gross_share"] <= 0.45
        c7 = conc_full["max_symbol_loss_to_gross"] is not None and conc_full["max_symbol_loss_to_gross"] <= 0.20
        decisions[variant] = {
            "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7,
            "C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7,
        }
        decisions[variant]["all_pass_3x"] = all(decisions[variant].values())

    passing = [v for v in VARIANTS if decisions[v]["all_pass_3x"]]
    selected = None
    stress = {}
    if passing:
        selected = max(passing, key=lambda v: results[v]["result"]["full"]["total_return"])
        for cost in (4.0, 5.0):
            stress_result, _ = _segmented(service, membership, r112, r229, selected, cost)
            stress[f"{cost:g}x"] = stress_result
        g8 = stress["4x"]["result"]["full"]["total_return"] > 0
        g9 = stress["5x"]["result"]["full"]["total_return"] > 0
        decisions[selected]["G8"] = g8
        decisions[selected]["G9"] = g9
        decisions[selected]["all_pass_deployment"] = decisions[selected]["all_pass_3x"] and g8 and g9
    else:
        decisions["all_pass_deployment"] = False

    payload = {
        "round": ROUND,
        "purpose": "Test whether fixed take-profit or shorter holding fixes R257 P&L concentration without destroying returns.",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r262_preregistration.json",
        "window": {"start": str(START), "end": str(END), "segments": [[str(s), str(e)] for s, e in SEGMENTS]},
        "execution": {
            "engine": "StrategyBacktestService",
            "strategy": SID,
            "variants": VARIANTS,
            "max_positions": 10,
            "position_sizing": "equal",
            "stop_loss": -0.20,
            "matching": "open_t+1",
            "cost_3x": "6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "cost_stress": "4x and 5x for the selected variant",
        },
        "control": {
            "strategy": SID,
            "result": control_result,
            "concentration_full": control_full,
        },
        "variants": {
            v: {
                "config": VARIANTS[v],
                "result": results[v]["result"],
                "runs": results[v]["runs"],
                "trade_count": len(all_trades[v]),
                "exit_reasons": {
                    reason: sum(1 for t in all_trades[v] if t.get("exit_reason") == reason)
                    for reason in sorted({t.get("exit_reason") for t in all_trades[v]})
                },
                "concentration": concentrations[v],
                "decision": decisions[v],
            }
            for v in VARIANTS
        },
        "stress": stress,
        "selected_variant": selected,
        "all_pass_deployment": bool(selected and decisions[selected].get("all_pass_deployment")),
        "decision_summary": decisions,
        "caveats": [
            "Fixed take-profit fills at the TP line when the daily high touches it; this is optimistic under daily bars.",
            "The full curve is stitched from two independently run segments; open positions reset at the boundary.",
            "Industry classification is the current snapshot, not historical PIT.",
            "R257 factor and sample were already used in R257-R261; this is not a fresh out-of-sample test.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
