#!/usr/bin/env python3
"""R269: compare score-weighted sizing with the frozen R266 equal-weight A leg."""

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
OUT = EVIDENCE / "r269_score_weight_sizing.json"
CACHE = DATA / "research" / "r269_cache"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
R266_CACHE = DATA / "research" / "r266_cache"
SID = "ai_hs300_macro_gate_quality"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
COSTS = {
    3: (0.0006, 0.0006, 0.001, 15.0),
    4: (0.0008, 0.0008, 0.001333333333, 20.0),
    5: (0.001, 0.001, 0.001666666667, 25.0),
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proof(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _load_control(cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    key = f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}"
    curve_path = R266_CACHE / f"{key}.parquet"
    trades_path = R266_CACHE / f"{key}.trades.json"
    if not curve_path.exists() or not trades_path.exists():
        raise FileNotFoundError(f"R266 equal-weight control missing for {cost}x")
    curve = (
        pl.read_parquet(curve_path)
        .with_columns(pl.col("date").cast(pl.Date))
        .sort("date")
    )
    trades = json.loads(trades_path.read_text(encoding="utf-8"))
    return curve, trades, {"curve": _proof(curve_path), "trades": _proof(trades_path)}


def _run_candidate(service, membership, r112, cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{cost}x_score_weight_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and trades_path.exists():
        curve = (
            pl.read_parquet(curve_path)
            .with_columns(pl.col("date").cast(pl.Date))
            .sort("date")
        )
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return curve, trades, {
            "cached": True,
            "curve": _proof(curve_path),
            "trades": _proof(trades_path),
        }

    fee, commission, stamp_tax, slippage = COSTS[cost]
    config = r112.StrategyBacktestConfig(
        strategy_id=SID,
        symbols=None,
        start=START,
        end=END,
        params={},
        overrides={},
        matching="open_t+1",
        fees_pct=fee,
        commission_pct=commission,
        stamp_tax_pct=stamp_tax,
        slippage_bps=slippage,
        max_positions=10,
        position_sizing="score_weight",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(config)
    if result.error:
        raise RuntimeError(f"{SID} score_weight {cost}x: {result.error}")

    curve = r112._curve_frame(result.equity_curve)
    trades = [
        {
            "symbol": trade.get("symbol"),
            "name": trade.get("name"),
            "entry_date": str(trade.get("entry_date")),
            "exit_date": str(trade.get("exit_date")),
            "pnl_pct": trade.get("pnl_pct"),
            "pnl_amount": trade.get("pnl_amount"),
            "exit_reason": trade.get("exit_reason"),
        }
        for trade in result.trades
    ]
    curve.write_parquet(curve_path)
    trades_path.write_text(
        json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    info = {
        "cached": False,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "stats": result.stats,
        "curve": _proof(curve_path),
        "trades": _proof(trades_path),
    }
    print(f"ran {SID} score_weight {cost}x: trades={len(trades)}", flush=True)
    return curve, trades, info


def _concentration_diagnostics(aggregate: dict) -> dict:
    return {
        "distinct_symbols": aggregate["distinct_symbols"],
        "n_trades": aggregate["n_trades"],
        "total_pnl_amount": aggregate["total_pnl_amount"],
        "gross_profit": aggregate["gross_profit"],
        "gross_loss": aggregate["gross_loss"],
        "net_to_gross": aggregate["net_to_gross"],
        "net_shares": {
            key: aggregate[key]
            for key in ("top1_net_share", "top3_net_share", "top5_net_share")
        },
        "gross_profit_shares": {
            key: aggregate[key]
            for key in ("top1_gross_share", "top3_gross_share", "top5_gross_share")
        },
        "top_industry_gross_share": aggregate["top_industry_gross_share"],
        "max_symbol_loss_to_gross": aggregate["max_symbol_loss_to_gross"],
        "top_symbols": aggregate["top5_symbols"],
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r269")
    r229 = _load(R229_SCRIPT, "r229_r269")
    r239 = _load(R239_SCRIPT, "r239_r269")
    control_evidence = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    if control_evidence.get("decision", {}).get("P1") is not True:
        raise RuntimeError("R266 equal-weight control failed its frozen parity gate")
    if control_evidence.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 control has no successful panel registration")

    controls = {}
    control_proofs = {}
    for cost in COSTS:
        curve, trades, proofs = _load_control(cost)
        controls[cost] = (curve, trades)
        control_proofs[str(cost)] = proofs
    p1 = (
        control_proofs["3"]["curve"]["sha256"]
        == control_evidence["runs"]["3x"]["curve_cache"]["sha256"]
        and control_evidence["parity_vs_r265"]["pass"] is True
    )

    membership = r112._historical_membership()
    service = r112._service()
    candidates = {}
    runs = {}
    for cost in COSTS:
        curve, trades, info = _run_candidate(service, membership, r112, cost)
        candidates[cost] = (curve, trades)
        runs[f"{cost}x"] = info

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    control_results = {}
    candidate_results = {}
    aggregates = {}
    for cost in COSTS:
        control_curve, control_trades = controls[cost]
        candidate_curve, candidate_trades = candidates[cost]
        control_stats = r229._result(r112, control_curve)
        candidate_stats = r229._result(r112, candidate_curve)
        control_concentration = r239._aggregate(control_trades, industry)
        candidate_concentration = r239._aggregate(candidate_trades, industry)
        aggregates[("control", cost)] = control_concentration
        aggregates[("candidate", cost)] = candidate_concentration
        control_results[str(cost)] = {
            "full": control_stats["full"],
            "rolling_1y": control_stats["rolling_1y"],
            "concentration": _concentration_diagnostics(control_concentration),
            "cache_proofs": control_proofs[str(cost)],
        }
        candidate_results[str(cost)] = {
            "full": candidate_stats["full"],
            "rolling_1y": candidate_stats["rolling_1y"],
            "recent_12m": candidate_stats["recent_12m"],
            "validation": r112._stats(
                candidate_curve.filter(pl.col("date") >= VALIDATION_START)
            ),
            "concentration": _concentration_diagnostics(candidate_concentration),
        }

    _, candidate_3x_trades = candidates[3]
    candidate_concentration = aggregates[("candidate", 3)]
    control_concentration = aggregates[("control", 3)]
    validation_trades = [
        trade
        for trade in candidate_3x_trades
        if str(trade.get("entry_date", ""))[:10] >= VALIDATION_START.isoformat()
    ]
    validation_concentration = r239._aggregate(validation_trades, industry)
    validation_positive = sum(
        row["pnl_amount"] > 0
        for row in validation_concentration["per_symbol"]
    )
    control_full = control_results["3"]["full"]
    candidate_full = candidate_results["3"]["full"]
    candidate_validation = candidate_results["3"]["validation"]
    decision = {
        "P1": p1,
        "P2": None,
        "G1": candidate_full["total_return"] > 0
        and candidate_full["total_return"] >= control_full["total_return"] * 0.70,
        "G2": candidate_full["mdd"] >= -0.25,
        "G3": candidate_results["3"]["rolling_1y"]["positive_rate"] >= 0.70,
        "G4": candidate_validation["total_return"] > 0,
        "G5": candidate_validation["annual"] >= 0.05,
        "G6": candidate_results["4"]["full"]["total_return"] > 0,
        "G7": candidate_results["5"]["full"]["total_return"] > 0,
        "R1": candidate_full["sharpe"] >= control_full["sharpe"],
        "R2": candidate_full["mdd"] >= control_full["mdd"] - 0.03,
        "C1": candidate_concentration["distinct_symbols"] >= 30,
        "C2": candidate_concentration["total_pnl_amount"] > 0
        and candidate_concentration["top1_net_share"] <= 0.25,
        "C3": candidate_concentration["total_pnl_amount"] > 0
        and candidate_concentration["top3_net_share"] <= 0.55,
        "C4": candidate_concentration["total_pnl_amount"] > 0
        and candidate_concentration["top5_net_share"] <= 0.75,
        "C5": validation_positive >= 8,
        "C6": candidate_concentration["top_industry_gross_share"] is not None
        and candidate_concentration["top_industry_gross_share"] <= 0.45,
        "C7": candidate_concentration["max_symbol_loss_to_gross"] is not None
        and candidate_concentration["max_symbol_loss_to_gross"] <= 0.20,
    }
    all_pass_without_panel = all(
        value is True for key, value in decision.items() if key != "P2"
    )
    payload = {
        "round": "R269",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r269_preregistration.json",
        "strategy": SID,
        "window": {
            "start": str(START),
            "end": str(END),
            "discovery_end": str(DISCOVERY_END),
            "validation_start": str(VALIDATION_START),
        },
        "execution": {
            "service": "StrategyBacktestService -> BacktestEngine",
            "matching": "open_t+1",
            "costs": ["3x", "4x", "5x"],
            "max_positions": 10,
            "position_sizing_candidate": "score_weight",
            "position_sizing_control": "equal",
            "membership": "r78_daily_membership",
        },
        "runs": runs,
        "equal_weight_control": control_results,
        "score_weight_candidate": candidate_results,
        "validation_positive_symbols": validation_positive,
        "concentration_validation": _concentration_diagnostics(validation_concentration),
        "decision": decision,
        "all_pass_without_panel": all_pass_without_panel,
        "panel_check_pending": True,
        "panel_registration": None,
        "all_pass": False,
        "paper_trading_candidate": False,
        "provenance": {
            "strategy_source": _proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "membership": _proof(r112.MEMBERSHIP),
            "r266_control_evidence": _proof(R266_RESULT),
            "r112_runner": _proof(R112_SCRIPT),
            "r269_runner": _proof(Path(__file__).resolve()),
            "strategy_backtest_service": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py"),
            "backtest_engine": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "concentration_aggregator": _proof(R239_SCRIPT),
            "stats_helper": _proof(R229_SCRIPT),
            "industry_snapshot": _proof(DATA / "ext_data" / "ext_hy_ths" / "part.parquet"),
        },
        "known_limitations": [
            "Discovery/validation are not fully independent because this strategy was used in earlier research.",
            "The existing engine caps each selected position at the equal-weight target; score_weight can reduce invested exposure.",
            "Industry classification is the current snapshot, not historical point-in-time data.",
            "Panel registration does not constitute production approval.",
        ],
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "control_3x": control_full,
                "candidate_3x": candidate_full,
                "candidate_4x_return": candidate_results["4"]["full"]["total_return"],
                "candidate_5x_return": candidate_results["5"]["full"]["total_return"],
                "control_concentration": _concentration_diagnostics(control_concentration),
                "candidate_concentration": _concentration_diagnostics(candidate_concentration),
                "decision": decision,
                "all_pass_without_panel": all_pass_without_panel,
                "saved": str(OUT),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
