#!/usr/bin/env python3
"""R266: validate the A leg as a standalone low-volatility candidate."""

from __future__ import annotations

import hashlib
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
OUT = EVIDENCE / "r266_a_leg_validation.json"
CACHE = DATA / "research" / "r266_cache"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
SID = "ai_hs300_macro_gate_quality"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
R265_CURVE = DATA / "research" / "r265_cache" / f"{SID}_2021-06-01_2026-10-16.parquet"
PANEL_EVIDENCE = EVIDENCE / "r266_panel_registration.json"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _file_proof(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _run(service, membership, r112, cost: float):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{cost:g}x_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and trades_path.exists():
        return (
            pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"),
            json.loads(trades_path.read_text(encoding="utf-8")),
            {
                "cached": True,
                "max_positions": 10,
                "curve_cache": _file_proof(curve_path),
                "trades_cache": _file_proof(trades_path),
            },
        )
    scale = cost / 3.0
    cfg = r112.StrategyBacktestConfig(
        strategy_id=SID, symbols=None, start=START, end=END,
        params={}, overrides={}, matching="open_t+1",
        fees_pct=0.0006 * scale, commission_pct=0.0006 * scale,
        stamp_tax_pct=0.001 * scale, slippage_bps=15.0 * scale,
        max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{SID} {cost}x: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [
        {
            "symbol": t.get("symbol"), "name": t.get("name"),
            "entry_date": str(t.get("entry_date")), "exit_date": str(t.get("exit_date")),
            "pnl_pct": t.get("pnl_pct"), "pnl_amount": t.get("pnl_amount"),
            "exit_reason": t.get("exit_reason"),
        }
        for t in result.trades
    ]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"ran {SID} {cost:g}x: days={curve.height} trades={len(trades)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
    return curve, trades, {
        "cached": False,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "max_positions": 10,
        "curve_cache": _file_proof(curve_path),
        "trades_cache": _file_proof(trades_path),
    }


def _parity(cached_path: Path, rerun: pl.DataFrame) -> dict:
    cached = pl.read_parquet(cached_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    c0 = float(cached["value"][0])
    r0 = float(rerun["value"][0])
    joined = cached.rename({"value": "cached"}).join(rerun.rename({"value": "rerun"}), on="date", how="inner").sort("date")
    joined = joined.with_columns((pl.col("cached") / c0).alias("cached_rebased"), (pl.col("rerun") / r0).alias("rerun_rebased"))
    diff = (joined["cached_rebased"] - joined["rerun_rebased"]).abs()
    return {
        "days": joined.height,
        "cached_total_return": round(float(cached["value"][-1] / cached["value"][0] - 1.0), 6),
        "rerun_total_return": round(float(rerun["value"][-1] / rerun["value"][0] - 1.0), 6),
        "max_abs_diff": round(float(diff.max() or 0.0), 12),
        "pass": bool(joined.height > 0 and float(diff.max() or 0.0) <= 1e-6),
    }


def _yearly(curve: pl.DataFrame) -> dict:
    out = {}
    for year in sorted({d.year for d in curve["date"].to_list()}):
        part = curve.filter(pl.col("date").dt.year() == year).sort("date")
        if part.height >= 2:
            out[str(year)] = round(float(part["value"][-1] / part["value"][0] - 1.0), 6)
    return out


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r266")
    r229 = _load(R229_SCRIPT, "r229_r266")
    r239 = _load(R239_SCRIPT, "r239_r266")
    membership = r112._historical_membership()
    service = r112._service()

    curve_3x, trades_3x, run_info = _run(service, membership, r112, 3.0)
    curve_4x, _, run_4x = _run(service, membership, r112, 4.0)
    curve_5x, _, run_5x = _run(service, membership, r112, 5.0)

    result = r229._result(r112, curve_3x)
    discovery = r112._stats(curve_3x.filter((pl.col("date") >= START) & (pl.col("date") <= DISCOVERY_END)))
    validation = r112._stats(curve_3x.filter((pl.col("date") >= VALIDATION_START) & (pl.col("date") <= END)))

    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    concentration = r239._aggregate(trades_3x, industry)
    validation_trades = [t for t in trades_3x if str(t.get("entry_date", ""))[:10] >= VALIDATION_START.isoformat()]
    validation_conc = r239._aggregate(validation_trades, industry)
    validation_positive = sum(1 for row in validation_conc["per_symbol"] if row["pnl_amount"] > 0)

    p1_result = _parity(R265_CURVE, curve_3x) if R265_CURVE.exists() else None
    p1 = bool(p1_result and p1_result["pass"])
    g1 = result["full"]["total_return"] > 0
    g2 = result["full"]["mdd"] >= -0.25
    g3 = (result["rolling_1y"]["positive_rate"] or 0.0) >= 0.70
    g4 = validation["total_return"] > 0
    g5 = validation["annual"] >= 0.05
    g6 = r112._stats(curve_4x)["total_return"] > 0
    g7 = r112._stats(curve_5x)["total_return"] > 0
    c1 = concentration["distinct_symbols"] >= 30
    c2 = concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25
    c3 = concentration["total_pnl_amount"] > 0 and concentration["top3_net_share"] <= 0.55
    c4 = concentration["total_pnl_amount"] > 0 and concentration["top5_net_share"] <= 0.75
    c5 = validation_positive >= 8
    c6 = concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45
    c7 = concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20
    panel = (
        json.loads(PANEL_EVIDENCE.read_text(encoding="utf-8"))
        if PANEL_EVIDENCE.exists()
        else None
    )
    p2 = bool(panel and panel.get("strategy_panel_ready")) if panel else None
    decision = {"P1": p1, "P2": p2, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5, "G6": g6, "G7": g7, "C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7}
    all_pass_without_panel = all(
        passed for gate, passed in decision.items() if gate != "P2"
    )
    all_pass = all_pass_without_panel and p2 is True

    payload = {
        "round": "R266",
        "purpose": "Standalone validation of the R131 A leg on current data.",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r266_preregistration.json",
        "strategy": SID,
        "window": {"start": str(START), "end": str(END), "discovery_end": str(DISCOVERY_END), "validation_start": str(VALIDATION_START)},
        "execution": {"service": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "cost_3x": "3x", "cost_stress": ["4x", "5x"], "max_positions": 10, "position_sizing": "equal"},
        "runs": {"3x": run_info, "4x": run_4x, "5x": run_5x},
        "full": result["full"],
        "rolling_1y": result["rolling_1y"],
        "recent_12m": result["recent_12m"],
        "discovery": discovery,
        "validation": validation,
        "yearly": _yearly(curve_3x),
        "stress": {"4x": r112._stats(curve_4x), "5x": r112._stats(curve_5x)},
        "parity_vs_r265": p1_result,
        "provenance": {
            "strategy_source": _file_proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "historical_membership": _file_proof(r112.MEMBERSHIP),
            "r265_reference_curve": _file_proof(R265_CURVE) if R265_CURVE.exists() else None,
            "research_backtest_runner": _file_proof(R112_SCRIPT),
            "r266_runner": _file_proof(Path(__file__).resolve()),
            "strategy_backtest_service": _file_proof(ROOT / "backend" / "app" / "backtest" / "strategy.py"),
            "backtest_engine": _file_proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "concentration_aggregator": _file_proof(R239_SCRIPT),
            "cost_and_curve_statistics": _file_proof(R229_SCRIPT),
            "industry_snapshot": _file_proof(
                DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
            ),
        },
        "concentration_full": concentration,
        "concentration_validation": validation_conc,
        "validation_positive_symbols": validation_positive,
        "decision": decision,
        "all_pass_without_panel": all_pass_without_panel,
        "panel_check_pending": panel is None,
        "panel_registration": (
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
            "r266_panel_registration.json"
            if panel is not None else None
        ),
        "all_pass": all_pass,
        "paper_trading_candidate": all_pass,
        "caveats": [
            "Discovery/validation split is not fully independent; the strategy was designed in earlier research.",
            "Macro gate inputs can drift with source data revisions.",
            "Industry classification is the current snapshot.",
            "The A leg has no fixed stop loss and holds up to 35 days.",
            "Paper trading is not production deployment."
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "full": {k: result["full"][k] for k in ("total_return", "annual", "mdd", "sharpe")},
        "rolling_1y": result["rolling_1y"],
        "validation": {k: validation[k] for k in ("total_return", "annual", "mdd", "sharpe")},
        "stress": {"4x": r112._stats(curve_4x)["total_return"], "5x": r112._stats(curve_5x)["total_return"]},
        "concentration": {k: concentration.get(k) for k in ("distinct_symbols", "top1_net_share", "top3_net_share", "top5_net_share", "top_industry_gross_share", "max_symbol_loss_to_gross")},
        "decision": decision,
        "all_pass_without_panel": payload["all_pass_without_panel"],
        "all_pass": payload["all_pass"],
    }, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
