#!/usr/bin/env python3
"""R268: test a 15-position capacity between the 10- and 20-position runs."""

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
OUT = EVIDENCE / "r268_position_capacity.json"
CACHE = DATA / "research" / "r268_cache"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R267_SCRIPT = ROOT / "scripts" / "backtest_r267_position_capacity.py"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
R267_RESULT = EVIDENCE / "r267_position_capacity.json"
R266_CACHE = DATA / "research" / "r266_cache"
R267_CACHE = DATA / "research" / "r267_cache"
SID = "ai_hs300_macro_gate_quality"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
MAX_POSITIONS = 15


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


def _read_15(service, membership, r112, cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{cost}x_mp{MAX_POSITIONS}_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    if curve_path.exists() and trades_path.exists():
        return (
            pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"),
            json.loads(trades_path.read_text(encoding="utf-8")),
            {"cached": True, "curve": _proof(curve_path), "trades": _proof(trades_path)},
        )

    scale = cost / 3.0
    config = r112.StrategyBacktestConfig(
        strategy_id=SID,
        symbols=None,
        start=START,
        end=END,
        params={},
        overrides={},
        matching="open_t+1",
        fees_pct=0.0006 * scale,
        commission_pct=0.0006 * scale,
        stamp_tax_pct=0.001 * scale,
        slippage_bps=15.0 * scale,
        max_positions=MAX_POSITIONS,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(config)
    if result.error:
        raise RuntimeError(f"{SID} 15 positions {cost}x: {result.error}")

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
        "max_positions": MAX_POSITIONS,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "curve": _proof(curve_path),
        "trades": _proof(trades_path),
    }
    print(
        f"ran {SID} 15 positions {cost}x: trades={len(trades)}",
        flush=True,
    )
    return curve, trades, info


def _cache_pair(cache_dir: Path, cost: int, capacity: int) -> tuple[pl.DataFrame, list[dict], dict]:
    suffix = f"_{cost}x" if capacity == 10 else f"_{cost}x_mp{capacity}"
    key = f"{SID}{suffix}_{START.isoformat()}_{END.isoformat()}"
    curve_path = cache_dir / f"{key}.parquet"
    trades_path = cache_dir / f"{key}.trades.json"
    if not curve_path.exists() or not trades_path.exists():
        raise FileNotFoundError(f"{capacity}-position {cost}x control cache missing")
    return (
        pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"),
        json.loads(trades_path.read_text(encoding="utf-8")),
        {"curve": _proof(curve_path), "trades": _proof(trades_path)},
    )


def _concentration_summary(aggregate: dict) -> dict:
    return {
        key: aggregate[key]
        for key in (
            "n_trades",
            "distinct_symbols",
            "total_pnl_amount",
            "top1_net_share",
            "top3_net_share",
            "top5_net_share",
            "top_industry_gross_share",
            "max_symbol_loss_to_gross",
        )
    }


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r268")
    r229 = _load(R229_SCRIPT, "r229_r268")
    r239 = _load(R239_SCRIPT, "r239_r268")
    r266_result = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    r267_result = json.loads(R267_RESULT.read_text(encoding="utf-8"))
    if r266_result.get("decision", {}).get("P1") is not True:
        raise RuntimeError("R266 failed frozen curve parity")
    if r266_result.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 strategy and factors are not registered in panel")
    if r267_result.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R267 strategy and factors are not registered in panel")

    controls = {}
    control_proofs = {}
    for capacity, cache_dir in ((10, R266_CACHE), (20, R267_CACHE)):
        curve, trades, proofs = _cache_pair(cache_dir, 3, capacity)
        controls[capacity] = (curve, trades)
        control_proofs[str(capacity)] = proofs

    r266_reference_hash = r266_result["runs"]["3x"]["curve_cache"]["sha256"]
    p1 = (
        control_proofs["10"]["curve"]["sha256"] == r266_reference_hash
        and r266_result["parity_vs_r265"]["pass"] is True
    )
    membership = r112._historical_membership()
    service = r112._service()
    curves = {}
    trades_by_cost = {}
    runs = {}
    for cost in (3, 4, 5):
        curve, trades, info = _read_15(service, membership, r112, cost)
        curves[cost] = curve
        trades_by_cost[cost] = trades
        runs[f"{cost}x"] = info

    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    capacity_results = {}
    aggregates = {}
    for capacity, (curve, trades) in controls.items():
        result = r229._result(r112, curve)
        aggregate = r239._aggregate(trades, industry)
        aggregates[capacity] = aggregate
        capacity_results[capacity] = {
            "full": result["full"],
            "rolling_1y": result["rolling_1y"],
            "validation": r112._stats(
                curve.filter(pl.col("date") >= VALIDATION_START)
            ),
            "concentration": _concentration_summary(aggregate),
        }
    candidate = r229._result(r112, curves[3])
    full_aggregate = r239._aggregate(trades_by_cost[3], industry)
    validation_curve = curves[3].filter(pl.col("date") >= VALIDATION_START)
    validation_stats = r112._stats(validation_curve)
    validation_trades = [
        trade for trade in trades_by_cost[3]
        if str(trade.get("entry_date", ""))[:10] >= VALIDATION_START.isoformat()
    ]
    validation_aggregate = r239._aggregate(validation_trades, industry)
    validation_positive = sum(
        row["pnl_amount"] > 0 for row in validation_aggregate["per_symbol"]
    )

    base_return = capacity_results[10]["full"]["total_return"]
    candidate_return = candidate["full"]["total_return"]
    decision = {
        "P1": p1,
        "P2": None,
        "G1": candidate_return > 0 and candidate_return >= base_return * 0.70,
        "G2": candidate["full"]["mdd"] >= -0.25,
        "G3": candidate["rolling_1y"]["positive_rate"] >= 0.70,
        "G4": validation_stats["total_return"] > 0,
        "G5": validation_stats["annual"] >= 0.05,
        "G6": r112._stats(curves[4])["total_return"] > 0,
        "G7": r112._stats(curves[5])["total_return"] > 0,
        "D1": full_aggregate["distinct_symbols"] >= 80,
        "C1": full_aggregate["distinct_symbols"] >= 30,
        "C2": full_aggregate["total_pnl_amount"] > 0
        and full_aggregate["top1_net_share"] <= 0.25,
        "C3": full_aggregate["total_pnl_amount"] > 0
        and full_aggregate["top3_net_share"] <= 0.55,
        "C4": full_aggregate["total_pnl_amount"] > 0
        and full_aggregate["top5_net_share"] <= 0.75,
        "C5": validation_positive >= 8,
        "C6": full_aggregate["top_industry_gross_share"] is not None
        and full_aggregate["top_industry_gross_share"] <= 0.45,
        "C7": full_aggregate["max_symbol_loss_to_gross"] is not None
        and full_aggregate["max_symbol_loss_to_gross"] <= 0.20,
    }

    reference_20_concentration = aggregates[20]
    reference_20_stats = capacity_results[20]
    reference_20_proofs = control_proofs["20"]
    candidate_position_count = full_aggregate["distinct_symbols"]
    comparison = {
        "return_retention_vs_10_positions": round(candidate_return / base_return, 6)
        if base_return else None,
        "return_vs_20_positions": round(
            candidate_return / reference_20_stats["full"]["total_return"], 6
        ) if reference_20_stats["full"]["total_return"] else None,
        "unique_symbols_change_vs_10": candidate_position_count
        - aggregates[10]["distinct_symbols"],
        "unique_symbols_change_vs_20": candidate_position_count
        - reference_20_concentration["distinct_symbols"],
        "top1_share_change_vs_10": round(
            full_aggregate["top1_net_share"] - aggregates[10]["top1_net_share"], 6
        ),
        "top3_share_change_vs_10": round(
            full_aggregate["top3_net_share"] - aggregates[10]["top3_net_share"], 6
        ),
        "top5_share_change_vs_10": round(
            full_aggregate["top5_net_share"] - aggregates[10]["top5_net_share"], 6
        ),
        "top1_share_change_vs_20": round(
            full_aggregate["top1_net_share"]
            - reference_20_concentration["top1_net_share"], 6
        ),
        "top3_share_change_vs_20": round(
            full_aggregate["top3_net_share"]
            - reference_20_concentration["top3_net_share"], 6
        ),
        "top5_share_change_vs_20": round(
            full_aggregate["top5_net_share"]
            - reference_20_concentration["top5_net_share"], 6
        ),
    }
    panel = (
        json.loads((EVIDENCE / "r268_panel_registration.json").read_text(encoding="utf-8"))
        if (EVIDENCE / "r268_panel_registration.json").exists()
        else None
    )
    p2 = bool(panel and panel.get("strategy_panel_ready")) if panel else None
    decision["P2"] = p2
    all_pass = all(value is True for value in decision.values())

    payload = {
        "round": "R268",
        "preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r268_preregistration.json",
        "strategy": SID,
        "window": {"start": str(START), "end": str(END), "discovery_end": str(DISCOVERY_END), "validation_start": str(VALIDATION_START)},
        "execution": {"service": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "costs": ["3x", "4x", "5x"], "max_positions_candidate": MAX_POSITIONS, "position_sizing": "equal", "membership": "r78_daily_membership"},
        "runs": runs,
        "control_10_positions": capacity_results[10],
        "control_20_positions": {
            **capacity_results[20],
            "cache_proofs": {"curve": reference_20_proofs["curve"], "trades": reference_20_proofs["trades"]},
        },
        "candidate_15_positions": {
            "full": candidate["full"],
            "rolling_1y": candidate["rolling_1y"],
            "recent_12m": candidate["recent_12m"],
            "discovery": r112._stats(
                curves[3].filter((pl.col("date") >= START) & (pl.col("date") <= DISCOVERY_END))
            ),
            "validation": validation_stats,
            "stress": {"4x": r112._stats(curves[4]), "5x": r112._stats(curves[5])},
            "concentration_full": full_aggregate,
            "concentration_validation": validation_aggregate,
            "validation_positive_symbols": validation_positive,
        },
        "comparison": comparison,
        "control_proofs": {
            "10_positions": control_proofs["10"],
            "20_positions": {"curve": reference_20_proofs["curve"], "trades": reference_20_proofs["trades"]},
        },
        "provenance": {
            "strategy_source": _proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "membership": _proof(r112.MEMBERSHIP),
            "r112_service_factory": _proof(R112_SCRIPT),
            "r267_runner_and_control": _proof(R267_SCRIPT),
            "r268_runner": _proof(Path(__file__).resolve()),
            "strategy_backtest_service": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py"),
            "backtest_engine": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "concentration_aggregator": _proof(R239_SCRIPT),
            "stats_helper": _proof(R229_SCRIPT),
            "industry_snapshot": _proof(DATA / "ext_data" / "ext_hy_ths" / "part.parquet"),
        },
        "decision": decision,
        "panel_check_pending": panel is None,
        "panel_registration": (
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
            "r268_panel_registration.json" if panel is not None else None
        ),
        "all_pass": all_pass,
        "paper_trading_candidate": all_pass,
        "caveats": [
            "The 15-position capacity follows observed R267 results and is not an independent confirmation.",
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
                "returns": {
                    "10_positions": capacity_results[10]["full"]["total_return"],
                    "15_positions": candidate_return,
                    "20_positions": capacity_results[20]["full"]["total_return"],
                },
                "candidate_15": {
                    "full": candidate["full"],
                    "validation": validation_stats,
                    "rolling_1y_positive_rate": candidate["rolling_1y"]["positive_rate"],
                    "4x": r112._stats(curves[4])["total_return"],
                    "5x": r112._stats(curves[5])["total_return"],
                },
                "concentration": _concentration_summary(full_aggregate),
                "comparison": comparison,
                "decision": decision,
                "all_pass": all_pass,
                "saved": str(OUT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
