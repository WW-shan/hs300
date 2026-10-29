#!/usr/bin/env python3
"""R270: preregistered fixed-stop loss-management test for the A sleeve."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import statistics
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.services.industry_map import load_industry_l1

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r270_stop_loss_scan.json"
PREREG = EVIDENCE / "r270_preregistration.json"
PREREG_HASH_FILE = EVIDENCE / "r270_preregistration.sha256"
CACHE = DATA / "research" / "r270_cache_final"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
R266_CACHE = DATA / "research" / "r266_cache"
SID = "ai_hs300_macro_gate_quality"
ROUND_ID = "R270"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
COSTS = {
    3: (0.0006, 0.0006, 0.001, 15.0),
    4: (0.0008, 0.0008, 0.001333333333, 20.0),
    5: (0.001, 0.001, 0.001666666667, 25.0),
}
STOP_LOSSES = {"sl10": 0.10, "sl15": 0.15, "sl20": 0.20}


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


def _run_candidate(
    service,
    membership: pl.DataFrame,
    r112,
    cost: int,
    stop_id: str,
    stop_loss: float,
) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{SID}_{stop_id}_{cost}x_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    manifest_path = CACHE / f"{key}.manifest.json"
    manifest = {
        "round": ROUND_ID,
        "strategy": SID,
        "stop_loss": stop_loss,
        "cost_multiplier": cost,
        "window": [str(START), str(END)],
        "matching": "open_t+1",
        "max_positions": 10,
        "position_sizing": "equal",
        "membership_sha256": _proof(r112.MEMBERSHIP)["sha256"],
        "strategy_source_sha256": _proof(DATA / "strategies" / "ai" / f"{SID}.py")["sha256"],
        "strategy_service_sha256": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py")["sha256"],
        "backtest_engine_sha256": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py")["sha256"],
        "research_runner_sha256": _proof(R112_SCRIPT)["sha256"],
        "r266_control_curve_sha256": _proof(
            R266_CACHE / f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}.parquet"
        )["sha256"],
        "r266_control_trades_sha256": _proof(
            R266_CACHE / f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}.trades.json"
        )["sha256"],
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    cache_paths = (curve_path, trades_path, manifest_path)
    if any(path.exists() for path in cache_paths):
        if not all(path.exists() for path in cache_paths):
            raise RuntimeError(f"incomplete R270 cache for {key}; refusing partial reuse")
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise RuntimeError(f"R270 cache provenance mismatch for {key}")
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return curve, trades, {
            "cached": True,
            "curve": _proof(curve_path),
            "trades": _proof(trades_path),
            "cache_manifest": _proof(manifest_path),
        }

    fee, commission, stamp_tax, slippage = COSTS[cost]
    config = r112.StrategyBacktestConfig(
        strategy_id=SID,
        symbols=None,
        start=START,
        end=END,
        params={},
        overrides={"stop_loss": stop_loss},
        matching="open_t+1",
        fees_pct=fee,
        commission_pct=commission,
        stamp_tax_pct=stamp_tax,
        slippage_bps=slippage,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with r112._patched_membership(membership):
        result = service.run(config)
    if result.error:
        raise RuntimeError(f"{SID} {stop_id} {cost}x: {result.error}")

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
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    info = {
        "cached": False,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "system_stats": result.stats,
        "curve": _proof(curve_path),
        "trades": _proof(trades_path),
        "cache_manifest": _proof(manifest_path),
    }
    stop_exits = sum(row.get("exit_reason") == "stop_loss" for row in trades)
    print(f"ran {SID} {stop_id} {cost}x: trades={len(trades)} stop_exits={stop_exits}", flush=True)
    return curve, trades, info


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _trade_loss_summary(trades: list[dict]) -> dict:
    valid = [row for row in trades if row.get("pnl_pct") is not None]
    returns = [float(row["pnl_pct"]) for row in valid]
    losses = [value for value in returns if value < 0]
    mean_loss = round(statistics.mean(losses), 6) if losses else None
    return {
        "trades": len(valid),
        "wins": sum(value > 0 for value in returns),
        "losses": len(losses),
        "stop_loss_exits": sum(row.get("exit_reason") == "stop_loss" for row in valid),
        "mean_loss_pct": mean_loss,
        "mean_loss_magnitude_pct": -mean_loss if mean_loss is not None else None,
        "median_loss_pct": round(statistics.median(losses), 6) if losses else None,
        "q10_trade_return_pct": round(_quantile(returns, 0.10), 6) if returns else None,
        "worst_trade_return_pct": min(returns) if returns else None,
    }


def _period_returns(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    prior = curve.filter(pl.col("date") < start).sort("date")
    selected = curve.filter(
        pl.col("date").is_between(start, end, closed="both")
    ).sort("date")
    if prior.height:
        prior_row = prior.tail(1)
        selected = pl.concat([prior_row, selected]).unique(subset=["date"], keep="last")
        selected = selected.sort("date")
    if selected.height < 2:
        raise RuntimeError(f"insufficient curve data for {start}~{end}")
    first, last = float(selected["value"][0]), float(selected["value"][-1])
    if first <= 0 or last <= 0:
        raise RuntimeError(f"invalid curve values for {start}~{end}")
    return selected.with_columns(
        (pl.col("value") / first * 1_000_000.0).alias("value")
    )


def _period_trades(
    trades: list[dict], start: date | None = None, end: date | None = None
) -> list[dict]:
    selected = []
    for trade in trades:
        raw = str(trade.get("entry_date", ""))[:10]
        if len(raw) != 10:
            continue
        entry = date.fromisoformat(raw)
        if start is not None and entry < start:
            continue
        if end is not None and entry > end:
            continue
        selected.append(trade)
    return selected


def _concentration(aggregate: dict) -> dict:
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


def _decide(control: dict, candidate: dict) -> dict:
    control_full = control["3"]["full"]
    control_validation = control["3"]["validation"]
    full = candidate["runs"]["3"]
    validation = full["validation"]
    concentration = full["concentration"]
    validation_concentration = candidate["concentration_validation"]
    control_loss_magnitude = control["3"]["trade_loss_summary"]["mean_loss_magnitude_pct"]
    control_validation_loss_magnitude = control["3"]["validation_trade_loss_summary"]["mean_loss_magnitude_pct"]
    candidate_loss_magnitude = candidate["trade_loss_summary"]["full"]["mean_loss_magnitude_pct"]
    candidate_validation_loss_magnitude = candidate["trade_loss_summary"]["validation"]["mean_loss_magnitude_pct"]
    total_pnl = concentration["total_pnl_amount"]
    return {
        "P1": True,
        "P2": None,
        "G1": (
            full["full"]["total_return"] > 0
            and full["full"]["total_return"]
            >= control_full["total_return"] * 0.70
        ),
        "G2": full["full"]["mdd"] >= -0.25,
        "G3": full["rolling_1y"]["positive_rate"] >= 0.70,
        "G4": validation["total_return"] > 0,
        "G5": validation["annual"] >= 0.05,
        "G6": candidate["runs"]["4"]["full"]["total_return"] > 0,
        "G7": candidate["runs"]["5"]["full"]["total_return"] > 0,
        "G8": validation["total_return"] >= control_validation["total_return"] * 0.70,
        "R1": full["full"]["sharpe"] >= control_full["sharpe"],
        "R2": full["full"]["mdd"] >= control_full["mdd"] - 0.03,
        "L1": (
            control_loss_magnitude is not None
            and candidate_loss_magnitude is not None
            and candidate_loss_magnitude <= control_loss_magnitude - 0.01
        ),
        "L2": (
            control_validation_loss_magnitude is not None
            and candidate_validation_loss_magnitude is not None
            and candidate_validation_loss_magnitude <= control_validation_loss_magnitude - 0.01
        ),
        "L3": (
            candidate["trade_loss_summary"]["full"]["worst_trade_return_pct"]
            > control["3"]["trade_loss_summary"]["worst_trade_return_pct"]
        ),
        "L4": (
            candidate["trade_loss_summary"]["validation"]["worst_trade_return_pct"]
            > control["3"]["validation_trade_loss_summary"]["worst_trade_return_pct"]
        ),
        "C1": concentration["distinct_symbols"] >= 30,
        "C2": total_pnl > 0 and concentration["net_shares"]["top1_net_share"] <= 0.25,
        "C3": total_pnl > 0 and concentration["net_shares"]["top3_net_share"] <= 0.55,
        "C4": total_pnl > 0 and concentration["net_shares"]["top5_net_share"] <= 0.75,
        "C5": candidate["validation_positive_symbols"] >= 8,
        "C6": (
            concentration["top_industry_gross_share"] is not None
            and concentration["top_industry_gross_share"] <= 0.45
        ),
        "C7": (
            concentration["max_symbol_loss_to_gross"] is not None
            and concentration["max_symbol_loss_to_gross"] <= 0.20
        ),
        "C8": (
            validation_concentration["total_pnl_amount"] > 0
            and validation_concentration["net_shares"]["top1_net_share"] <= 0.35
        ),
    }


def main() -> int:
    if not PREREG.exists():
        raise FileNotFoundError(f"R270 must be preregistered before the backtest: {PREREG}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen_prereg_hash = PREREG_HASH_FILE.read_text(encoding="ascii").strip()
    panel_registrar = ROOT / "scripts" / "register_r270_panel.py"
    if (
        prereg.get("round") != ROUND_ID
        or prereg.get("status") != "pre_registered_before_backtest"
        or hashlib.sha256(PREREG.read_bytes()).hexdigest() != frozen_prereg_hash
        or prereg.get("runner_sha256")
        != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        or prereg.get("panel_registrar_sha256")
        != hashlib.sha256(panel_registrar.read_bytes()).hexdigest()
    ):
        raise RuntimeError("R270 preregistration is missing or has an invalid status")

    r112 = _load(R112_SCRIPT, "r112_r270")
    r229 = _load(R229_SCRIPT, "r229_r270")
    r239 = _load(R239_SCRIPT, "r239_r270")
    control_evidence = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    if (
        control_evidence.get("decision", {}).get("P1") is not True
        or control_evidence.get("decision", {}).get("P2") is not True
    ):
        raise RuntimeError("R266 equal-weight control has not passed its frozen parity/panel gates")

    control_curves = {}
    control_trades = {}
    control_proofs = {}
    for cost in COSTS:
        curve, trades, proofs = _load_control(cost)
        expected_trades = control_evidence["runs"][f"{cost}x"]["trades_cache"]["sha256"]
        if proofs["trades"]["sha256"] != expected_trades:
            raise RuntimeError(f"R266 {cost}x trade cache does not match finalized evidence")
        control_curves[cost] = curve
        control_trades[cost] = trades
        control_proofs[str(cost)] = proofs
    p1 = control_evidence.get("parity_vs_r265", {}).get("pass") is True
    for cost in COSTS:
        finalized = control_evidence.get("runs", {}).get(f"{cost}x", {})
        p1 = p1 and (
            control_proofs[str(cost)]["curve"]["sha256"]
            == finalized.get("curve_cache", {}).get("sha256")
            and control_proofs[str(cost)]["trades"]["sha256"]
            == finalized.get("trades_cache", {}).get("sha256")
        )
    if not p1:
        raise RuntimeError("R266 control cache does not match finalized parity evidence")

    membership = r112._historical_membership()
    service = r112._service()
    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }
    control = {}
    for cost in COSTS:
        stats = r229._result(r112, control_curves[cost])
        aggregate = r239._aggregate(control_trades[cost], industry)
        validation_trades = _period_trades(control_trades[cost], VALIDATION_START, END)
        validation_aggregate = r239._aggregate(validation_trades, industry)
        control[str(cost)] = {
            "full": stats["full"],
            "rolling_1y": stats["rolling_1y"],
            "validation": r112._stats(
                _period_returns(control_curves[cost], VALIDATION_START, END)
            ),
            "concentration": _concentration(aggregate),
            "trade_loss_summary": _trade_loss_summary(control_trades[cost]),
            "validation_trade_loss_summary": _trade_loss_summary(validation_trades),
            "validation_concentration": _concentration(validation_aggregate),
            "cache_proofs": control_proofs[str(cost)],
        }

    candidate_results = {}
    candidate_runs = {}
    decisions = {}
    for stop_id, stop_loss in STOP_LOSSES.items():
        run_info = {}
        cost_results = {}
        aggregates = {}
        trades_by_cost = {}
        for cost in COSTS:
            curve, trades, info = _run_candidate(
                service, membership, r112, cost, stop_id, stop_loss
            )
            run_info[f"{cost}x"] = info
            trades_by_cost[cost] = trades
            aggregate = r239._aggregate(trades, industry)
            aggregates[cost] = aggregate
            stats = r229._result(r112, curve)
            cost_results[str(cost)] = {
                "full": stats["full"],
                "rolling_1y": stats["rolling_1y"],
                "recent_12m": stats["recent_12m"],
                "discovery": r112._stats(
                    _period_returns(curve, START, DISCOVERY_END)
                ),
                "validation": r112._stats(
                    _period_returns(curve, VALIDATION_START, END)
                ),
                "concentration": _concentration(aggregate),
                "trade_loss_summary": _trade_loss_summary(trades),
            }

        full_trades = trades_by_cost[3]
        validation_trades = _period_trades(full_trades, VALIDATION_START, END)
        validation_aggregate = r239._aggregate(validation_trades, industry)
        validation_positive = sum(
            row["pnl_amount"] > 0 for row in validation_aggregate["per_symbol"]
        )
        trade_loss = {
            "full": _trade_loss_summary(full_trades),
            "discovery": _trade_loss_summary(_period_trades(full_trades, end=DISCOVERY_END)),
            "validation": _trade_loss_summary(validation_trades),
        }
        candidate = {
            "stop_loss": stop_loss,
            "runs": cost_results,
            "validation_positive_symbols": validation_positive,
            "concentration_validation": _concentration(validation_aggregate),
            "validation_top1_net_share": validation_aggregate["top1_net_share"],
            "trade_loss_summary": trade_loss,
        }
        decision = _decide(control, candidate)
        candidate["decision"] = decision
        candidate["all_pass_without_panel"] = all(
            value is True for key, value in decision.items() if key != "P2"
        )
        candidate_results[stop_id] = candidate
        candidate_runs[stop_id] = run_info
        decisions[stop_id] = decision
        print(
            f"{stop_id}: return={cost_results['3']['full']['total_return']:.4%}, "
            f"MDD={cost_results['3']['full']['mdd']:.4%}, "
            f"mean_loss={trade_loss['full']['mean_loss_pct']}, "
            f"pass_without_panel={candidate['all_pass_without_panel']}",
            flush=True,
        )

    payload = {
        "round": "R270",
        "preregistration": PREREG.relative_to(ROOT).as_posix(),
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
            "risk_exit_execution": (
                "BacktestEngine daily OHLC: gap through stop fills at open, otherwise at stop line;"
                "same-day entry excluded and system tradability/limit blocks remain active"
            ),
            "costs": ["3x", "4x", "5x"],
            "max_positions": 10,
            "position_sizing": "equal",
            "membership": "r78_daily_membership",
            "only_changed_parameter": "fixed stop_loss override",
            "candidate_cache_directory": CACHE.relative_to(ROOT).as_posix(),
            "trade_period_attribution": "entry_date",
        },
        "control": control,
        "candidate_runs": candidate_runs,
        "candidates": candidate_results,
        "decision": decisions,
        "panel_check_pending": True,
        "panel_registration": None,
        "all_pass": False,
        "passing_variants": [],
        "paper_trading_candidate": False,
        "provenance": {
            "strategy_source": _proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "membership": _proof(r112.MEMBERSHIP),
            "r266_control_evidence": _proof(R266_RESULT),
            "r270_preregistration": _proof(PREREG),
            "r270_preregistration_hash": _proof(PREREG_HASH_FILE),
            "r112_runner": _proof(R112_SCRIPT),
            "r270_runner": _proof(Path(__file__).resolve()),
            "strategy_backtest_service": _proof(
                ROOT / "backend" / "app" / "backtest" / "strategy.py"
            ),
            "backtest_engine": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "concentration_aggregator": _proof(R239_SCRIPT),
            "stats_helper": _proof(R229_SCRIPT),
            "panel_registrar": _proof(panel_registrar),
            "industry_snapshot": _proof(DATA / "ext_data" / "ext_hy_ths" / "part.parquet"),
        },
        "known_limitations": [
            "An initial run with the same frozen variants was superseded before finalization after audit corrected the signed loss-improvement gate; this final run reruns all nine system backtests into a fresh cache namespace.",
            "The strategy and validation interval were studied in prior rounds; this is not an untouched holdout.",
            "The stop bracket was frozen from R269 discovery-segment loss quantiles before R270 runs.",
            "Daily OHLC cannot establish intraday event ordering when a stop and a price-limit condition coincide.",
            "Stop trigger/fill prices use the system strategy panel OHLC fields (forward-adjusted), not tick-level raw fills.",
            "Gap-through-stop losses can exceed the threshold; real fills can be worse than simulated fills.",
            "Historical HS300 membership is used, but industry classification remains a current snapshot.",
            "Discovery/validation trade attribution uses entry_date; trades spanning the boundary stay with their entry period.",
            "Only 290 baseline trades are available, creating sampling uncertainty in tail quantiles.",
            "Panel reload and historical backtests do not authorize production deployment.",
        ],
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"saved {OUT.relative_to(ROOT).as_posix()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
