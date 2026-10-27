#!/usr/bin/env python3
"""R267: test whether wider position capacity diversifies the A-leg returns."""

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
OUT = EVIDENCE / "r267_position_capacity.json"
CACHE = DATA / "research" / "r267_cache"
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
COSTS = {3: (0.0006, 0.0006, 0.001, 15.0), 4: (0.0008, 0.0008, 0.001333333333, 20.0), 5: (0.001, 0.001, 0.001666666667, 25.0)}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _proof(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _read_control(cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    key = f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}"
    curve_path = R266_CACHE / f"{key}.parquet"
    trades_path = R266_CACHE / f"{key}.trades.json"
    if not curve_path.exists() or not trades_path.exists():
        raise FileNotFoundError(f"R266 control cache missing for {cost}x")
    return (
        pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"),
        json.loads(trades_path.read_text(encoding="utf-8")),
        {"curve": _proof(curve_path), "trades": _proof(trades_path)},
    )


def _run(service, membership, r112, cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{cost}x_mp20_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and trades_path.exists():
        return (
            pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"),
            json.loads(trades_path.read_text(encoding="utf-8")),
            {"cached": True, "curve": _proof(curve_path), "trades": _proof(trades_path)},
        )

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
        max_positions=20,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(config)
    if result.error:
        raise RuntimeError(f"{SID} 20 positions {cost}x: {result.error}")

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
        "max_positions": 20,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "curve": _proof(curve_path),
        "trades": _proof(trades_path),
    }
    print(f"ran {SID} 20 positions {cost}x: trades={len(trades)}", flush=True)
    return curve, trades, info


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r267")
    r229 = _load(R229_SCRIPT, "r229_r267")
    r239 = _load(R239_SCRIPT, "r239_r267")
    control_result = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    control_curves = {}
    control_trades = {}
    control_proofs = {}
    for cost in COSTS:
        curve, trades, proofs = _read_control(cost)
        control_curves[cost] = curve
        control_trades[cost] = trades
        control_proofs[str(cost)] = proofs

    if control_result.get("parity_vs_r265", {}).get("pass") is not True:
        raise RuntimeError("R266 control did not pass its frozen curve parity gate")
    if control_result.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 control has no successful live panel registration")

    membership = r112._historical_membership()
    service = r112._service()
    candidate_curves = {}
    candidate_trades = {}
    runs = {}
    for cost in COSTS:
        curve, trades, info = _run(service, membership, r112, cost)
        candidate_curves[cost] = curve
        candidate_trades[cost] = trades
        runs[f"{cost}x"] = info

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    control_3x = r229._result(r112, control_curves[3])
    candidate_3x = r229._result(r112, candidate_curves[3])
    candidate_validation = candidate_curves[3].filter(pl.col("date") >= VALIDATION_START)
    control_concentration = r239._aggregate(control_trades[3], industry)
    concentration = r239._aggregate(candidate_trades[3], industry)
    validation_trades = [
        trade for trade in candidate_trades[3]
        if str(trade.get("entry_date", ""))[:10] >= VALIDATION_START.isoformat()
    ]
    validation_concentration = r239._aggregate(validation_trades, industry)
    validation_positive = sum(
        row["pnl_amount"] > 0 for row in validation_concentration["per_symbol"]
    )

    base_return = control_3x["full"]["total_return"]
    candidate_return = candidate_3x["full"]["total_return"]
    control_10_curve_proof = control_proofs["3"]["curve"]
    r266_curve_proof = control_result["runs"]["3x"]["curve_cache"]
    p1 = control_10_curve_proof["sha256"] == r266_curve_proof["sha256"]
    decision = {
        "P1": p1 and control_result["parity_vs_r265"]["pass"],
        "P2": None,
        "G1": candidate_return > 0 and candidate_return >= base_return * 0.70,
        "G2": candidate_3x["full"]["mdd"] >= -0.25,
        "G3": candidate_3x["rolling_1y"]["positive_rate"] >= 0.70,
        "G4": True,
        "G5": True,
        "G6": r112._stats(candidate_curves[4])["total_return"] > 0,
        "G7": r112._stats(candidate_curves[5])["total_return"] > 0,
        "D1": concentration["distinct_symbols"] >= 70,
        "C1": concentration["distinct_symbols"] >= 30,
        "C2": concentration["total_pnl_amount"] > 0
        and concentration["top1_net_share"] <= 0.25,
        "C3": concentration["total_pnl_amount"] > 0
        and concentration["top3_net_share"] <= 0.55,
        "C4": concentration["total_pnl_amount"] > 0
        and concentration["top5_net_share"] <= 0.75,
        "C5": validation_positive >= 8,
        "C6": concentration["top_industry_gross_share"] is not None
        and concentration["top_industry_gross_share"] <= 0.45,
        "C7": concentration["max_symbol_loss_to_gross"] is not None
        and concentration["max_symbol_loss_to_gross"] <= 0.20,
    }
    validation_stats = r112._stats(candidate_validation)
    decision["G4"] = validation_stats["total_return"] > 0
    decision["G5"] = validation_stats["annual"] >= 0.05

    payload = {
        "round": "R267",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r267_preregistration.json",
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
            "max_positions_candidate": 20,
            "max_positions_control": 10,
            "position_sizing": "equal",
            "membership": "r78_daily_membership",
        },
        "runs": runs,
        "control_10_positions": {
            "full": control_3x["full"],
            "rolling_1y": control_3x["rolling_1y"],
            "validation": r112._stats(
                control_curves[3].filter(pl.col("date") >= VALIDATION_START)
            ),
            "concentration": {
                key: control_concentration[key]
                for key in (
                    "n_trades",
                    "distinct_symbols",
                    "total_pnl_amount",
                    "top1_net_share",
                    "top3_net_share",
                    "top5_net_share",
                )
            },
            "cache_proofs": control_proofs,
        },
        "candidate_20_positions": {
            "full": candidate_3x["full"],
            "rolling_1y": candidate_3x["rolling_1y"],
            "recent_12m": candidate_3x["recent_12m"],
            "discovery": r112._stats(candidate_curves[3].filter(pl.col("date") <= DISCOVERY_END)),
            "validation": validation_stats,
            "stress": {
                "4x": r112._stats(candidate_curves[4]),
                "5x": r112._stats(candidate_curves[5]),
            },
            "concentration_full": concentration,
            "concentration_validation": validation_concentration,
            "validation_positive_symbols": validation_positive,
        },
        "comparison": {
            "full_return_retention_vs_10_positions": round(
                candidate_return / base_return, 6
            ) if base_return else None,
            "unique_symbols_change": concentration["distinct_symbols"]
            - control_concentration["distinct_symbols"],
            "top1_share_change": round(
                concentration["top1_net_share"]
                - control_concentration["top1_net_share"],
                6,
            ),
            "top3_share_change": round(
                concentration["top3_net_share"]
                - control_concentration["top3_net_share"],
                6,
            ),
            "top5_share_change": round(
                concentration["top5_net_share"]
                - control_concentration["top5_net_share"],
                6,
            ),
        },
        "provenance": {
            "strategy_source": _proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "membership": _proof(r112.MEMBERSHIP),
            "r112_service_factory": _proof(R112_SCRIPT),
            "r267_runner": _proof(Path(__file__).resolve()),
            "strategy_backtest_service": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py"),
            "backtest_engine": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "concentration_aggregator": _proof(R239_SCRIPT),
            "stats_helper": _proof(R229_SCRIPT),
            "industry_snapshot": _proof(
                DATA / "ext_data" / "ext_hy_ths" / "part.parquet"
            ),
        },
        "decision": decision,
        "panel_check_pending": True,
        "all_pass_without_panel": all(
            value for gate, value in decision.items() if gate != "P2"
        ),
        "all_pass": False,
        "paper_trading_candidate": False,
        "caveats": [
            "Discovery and validation are not fully independent.",
            "Industry classification uses the current snapshot.",
            "The strategy holds up to 35 trading days and has no fixed stop loss.",
        ],
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "control_return": base_return,
                "candidate_return": candidate_return,
                "candidate_validation": validation_stats,
                "stress": payload["candidate_20_positions"]["stress"],
                "concentration": {
                    key: concentration.get(key)
                    for key in (
                        "distinct_symbols",
                        "top1_net_share",
                        "top3_net_share",
                        "top5_net_share",
                        "top_industry_gross_share",
                    )
                },
                "comparison": payload["comparison"],
                "decision": decision,
                "all_pass_without_panel": payload["all_pass_without_panel"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
