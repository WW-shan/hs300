#!/usr/bin/env python3
"""R271: exploratory half-shrink industry-neutral scoring test for the A sleeve."""

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
OUT = EVIDENCE / "r271_shrink_neutralization_source_locked.json"
EXECUTION_SPEC = EVIDENCE / "r271_source_locked_execution_spec.json"
EXECUTION_SPEC_HASH_FILE = EVIDENCE / "r271_source_locked_execution_spec.sha256"
CACHE = DATA / "research" / "r271_cache_source_locked"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R229_SCRIPT = ROOT / "scripts" / "backtest_r229_ocf_margin.py"
R239_SCRIPT = ROOT / "scripts" / "backtest_r239_concentration_audit.py"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
R266_CACHE = DATA / "research" / "r266_cache"
SID = "ai_hs300_macro_gate_quality"
R271_SID = "ai_hs300_macro_gate_quality_r271_industry_shrink05"
ROUND_ID = "R271"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
DISCOVERY_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
COSTS = {
    3: (0.0006, 0.0006, 0.001, 15.0),
    4: (0.0008, 0.0008, 0.001333333333, 20.0),
    5: (0.001, 0.001, 0.001666666667, 25.0),
}
VARIANTS = {
    "control": {"neutralize": None},
    "industry_shrink_05": {
        "neutralize": {"industry": "industry_l1", "shrink": 0.5}
    },
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


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _loss_summary(trades: list[dict]) -> dict:
    returns = [float(row["pnl_pct"]) for row in trades if row.get("pnl_pct") is not None]
    losses = [value for value in returns if value < 0]
    mean_loss = statistics.mean(losses) if losses else None
    return {
        "trades": len(returns),
        "losses": len(losses),
        "mean_loss_pct": round(mean_loss, 6) if mean_loss is not None else None,
        "mean_loss_magnitude_pct": round(-mean_loss, 6) if mean_loss is not None else None,
        "q10_trade_return_pct": round(_quantile(returns, 0.10), 6) if returns else None,
        "worst_trade_return_pct": min(returns) if returns else None,
    }


def _period_returns(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    prior = curve.filter(pl.col("date") < start).sort("date")
    selected = curve.filter(pl.col("date").is_between(start, end, closed="both")).sort("date")
    if prior.height:
        selected = pl.concat([prior.tail(1), selected]).unique(subset=["date"], keep="last").sort("date")
    if selected.height < 2:
        raise RuntimeError(f"insufficient curve data for {start}~{end}")
    first = float(selected["value"][0])
    last = float(selected["value"][-1])
    if first <= 0 or last <= 0:
        raise RuntimeError(f"invalid curve values for {start}~{end}")
    return selected.with_columns((pl.col("value") / first * 1_000_000.0).alias("value"))


def _period_trades(trades: list[dict], start: date | None = None, end: date | None = None) -> list[dict]:
    result = []
    for row in trades:
        raw = str(row.get("entry_date", ""))[:10]
        if len(raw) != 10:
            continue
        entry = date.fromisoformat(raw)
        if start is not None and entry < start:
            continue
        if end is not None and entry > end:
            continue
        result.append(row)
    return result


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


def _run(service, membership: pl.DataFrame, r112, variant: str, cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    spec = VARIANTS[variant]
    strategy_id = R271_SID if variant == "industry_shrink_05" else SID
    key = f"{SID}_{variant}_{cost}x_{START.isoformat()}_{END.isoformat()}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    manifest_path = CACHE / f"{key}.manifest.json"
    overrides = {"neutralize": spec["neutralize"]} if spec["neutralize"] else {}
    manifest = {
        "round": ROUND_ID,
        "strategy": strategy_id,
        "variant": variant,
        "overrides": overrides,
        "cost_multiplier": cost,
        "window": [str(START), str(END)],
        "matching": "open_t+1",
        "max_positions": 10,
        "position_sizing": "equal",
        "membership_sha256": _proof(r112.MEMBERSHIP)["sha256"],
        "strategy_source_sha256": _proof(DATA / "strategies" / "ai" / f"{strategy_id}.py")["sha256"],
        "strategy_service_sha256": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py")["sha256"],
        "backtest_engine_sha256": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py")["sha256"],
        "scoring_source_sha256": _proof(ROOT / "backend" / "app" / "strategy" / "scoring.py")["sha256"],
        "industry_snapshot_sha256": _proof(DATA / "ext_data" / "ext_hy_ths" / "part.parquet")["sha256"],
        "research_runner_sha256": _proof(R112_SCRIPT)["sha256"],
        "r266_control_curve_sha256": _proof(R266_CACHE / f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}.parquet")["sha256"],
        "r266_control_trades_sha256": _proof(R266_CACHE / f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}.trades.json")["sha256"],
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    paths = (curve_path, trades_path, manifest_path)
    if any(path.exists() for path in paths):
        if not all(path.exists() for path in paths):
            raise RuntimeError(f"incomplete R271 cache for {key}; refusing partial reuse")
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise RuntimeError(f"R271 cache provenance mismatch for {key}")
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return curve, trades, {
            "cached": True,
            "curve": _proof(curve_path),
            "trades": _proof(trades_path),
            "cache_manifest": _proof(manifest_path),
        }

    fees, commission, stamp_tax, slippage = COSTS[cost]
    config = r112.StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=START,
        end=END,
        params={},
        overrides=overrides,
        matching="open_t+1",
        fees_pct=fees,
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
    elapsed = round(time.perf_counter() - started, 1)
    if result.error:
        raise RuntimeError(f"{variant} {cost}x: {result.error}")

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
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ran {variant} {cost}x: trades={len(trades)} elapsed={elapsed}s", flush=True)
    return curve, trades, {
        "cached": False,
        "elapsed_seconds": elapsed,
        "system_stats": result.stats,
        "curve": _proof(curve_path),
        "trades": _proof(trades_path),
        "cache_manifest": _proof(manifest_path),
    }


def _summarize_run(r112, r229, r239, curve: pl.DataFrame, trades: list[dict], industry: dict, cost: int) -> dict:
    stats = r229._result(r112, curve)
    validation_trades = _period_trades(trades, VALIDATION_START, END)
    return {
        "full": stats["full"],
        "rolling_1y": stats["rolling_1y"],
        "recent_12m": stats["recent_12m"],
        "discovery": r112._stats(_period_returns(curve, START, DISCOVERY_END)),
        "validation": r112._stats(_period_returns(curve, VALIDATION_START, END)),
        "concentration": _concentration(r239._aggregate(trades, industry)),
        "validation_concentration": _concentration(r239._aggregate(validation_trades, industry)),
        "trade_loss_summary": _loss_summary(trades),
        "validation_trade_loss_summary": _loss_summary(validation_trades),
        "cost_multiplier": cost,
    }


def _gate_delta(candidate: dict, control: dict, control_validation: dict) -> dict:
    run = candidate["runs"]["3x"]
    full = run["full"]
    validation = run["validation"]
    concentration = run["concentration"]
    validation_concentration = run["validation_concentration"]
    total_pnl = concentration["total_pnl_amount"]
    control_full = control["runs"]["3x"]["full"]
    control_loss = control["runs"]["3x"]["trade_loss_summary"]["mean_loss_magnitude_pct"]
    control_validation_loss = control["runs"]["3x"]["validation_trade_loss_summary"]["mean_loss_magnitude_pct"]
    loss = run["trade_loss_summary"]["mean_loss_magnitude_pct"]
    validation_loss = run["validation_trade_loss_summary"]["mean_loss_magnitude_pct"]
    decision = {
        "P1": True,
        "P2": None,
        "G1": full["total_return"] > 0 and full["total_return"] >= control_full["total_return"] * 0.70,
        "G2": full["mdd"] >= -0.25,
        "G3": run["rolling_1y"]["positive_rate"] >= 0.70,
        "G4": validation["total_return"] > 0,
        "G5": validation["annual"] >= 0.05,
        "G6": candidate["runs"]["4x"]["full"]["total_return"] > 0,
        "G7": candidate["runs"]["5x"]["full"]["total_return"] > 0,
        "G8": validation["total_return"] >= control_validation["total_return"] * 0.70,
        "R1": full["sharpe"] >= control_full["sharpe"],
        "R2": full["mdd"] >= control_full["mdd"] - 0.03,
        "C1": concentration["distinct_symbols"] >= 30,
        "C2": total_pnl > 0 and concentration["net_shares"]["top1_net_share"] <= 0.25,
        "C3": total_pnl > 0 and concentration["net_shares"]["top3_net_share"] <= 0.55,
        "C4": total_pnl > 0 and concentration["net_shares"]["top5_net_share"] <= 0.75,
        "C5": candidate["validation_positive_symbols"] >= 8,
        "C6": concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45,
        "C7": concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20,
        "C8": validation_concentration["total_pnl_amount"] > 0 and validation_concentration["net_shares"]["top1_net_share"] <= 0.35,
        "L1": control_loss is not None and loss is not None and loss <= control_loss - 0.01,
        "L2": control_validation_loss is not None and validation_loss is not None and validation_loss <= control_validation_loss - 0.01,
        "L3": run["trade_loss_summary"]["worst_trade_return_pct"] > control["runs"]["3x"]["trade_loss_summary"]["worst_trade_return_pct"],
        "L4": run["validation_trade_loss_summary"]["worst_trade_return_pct"] > control["runs"]["3x"]["validation_trade_loss_summary"]["worst_trade_return_pct"],
    }
    return {
        "thresholds_unchanged_from_r270": True,
        "decision": decision,
        "all_pass": all(value is True for value in decision.values()),
    }


def main() -> int:
    if not EXECUTION_SPEC.exists() or not EXECUTION_SPEC_HASH_FILE.exists():
        raise FileNotFoundError(f"R271 corrected exploratory execution spec required: {EXECUTION_SPEC}")
    prereg = json.loads(EXECUTION_SPEC.read_text(encoding="utf-8"))
    frozen_hash = EXECUTION_SPEC_HASH_FILE.read_text(encoding="ascii").split()[0]
    if (
        prereg.get("round") != ROUND_ID
        or prereg.get("status") != "source_locked_exploratory_spec_frozen_after_result_exposure"
        or hashlib.sha256(EXECUTION_SPEC.read_bytes()).hexdigest() != frozen_hash
        or prereg.get("runner_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        or prereg.get("panel_registrar_sha256")
        != hashlib.sha256((ROOT / "scripts" / "register_r271_corrected_panel.py").read_bytes()).hexdigest()
        or prereg.get("candidate_strategy_source_sha256")
        != _proof(DATA / "strategies" / "ai" / f"{R271_SID}.py")["sha256"]
        or prereg.get("execution", {}).get("candidate_cache_namespace")
        != CACHE.relative_to(ROOT).as_posix() + "/"
    ):
        raise RuntimeError("R271 exploratory execution spec or source hashes are invalid")

    r112 = _load(R112_SCRIPT, "r112_r271")
    r229 = _load(R229_SCRIPT, "r229_r271")
    r239 = _load(R239_SCRIPT, "r239_r271")
    control_evidence = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    if control_evidence.get("decision", {}).get("P1") is not True or control_evidence.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 equal-weight control parity/panel gates are not valid")

    membership = r112._historical_membership()
    service = r112._service()
    industry_frame = load_industry_l1(DATA)
    industry = {
        str(row["symbol"]): str(row["industry_l1"])
        for row in industry_frame.iter_rows(named=True)
        if row["industry_l1"]
    }

    controls: dict[str, dict] = {"runs": {}}
    control_proofs = {}
    for cost in COSTS:
        key = f"{SID}_{cost}x_{START.isoformat()}_{END.isoformat()}"
        curve_path = R266_CACHE / f"{key}.parquet"
        trades_path = R266_CACHE / f"{key}.trades.json"
        finalized = control_evidence["runs"][f"{cost}x"]
        if _proof(curve_path)["sha256"] != finalized["curve_cache"]["sha256"] or _proof(trades_path)["sha256"] != finalized["trades_cache"]["sha256"]:
            raise RuntimeError(f"R266 {cost}x control hash mismatch")
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        controls["runs"][f"{cost}x"] = _summarize_run(r112, r229, r239, curve, trades, industry, cost)
        control_proofs[f"{cost}x"] = {"curve": _proof(curve_path), "trades": _proof(trades_path)}

    variant_results = {}
    run_proofs = {}
    for variant in VARIANTS:
        runs = {}
        infos = {}
        trades_3x: list[dict] = []
        for cost in COSTS:
            curve, trades, info = _run(service, membership, r112, variant, cost)
            runs[f"{cost}x"] = _summarize_run(r112, r229, r239, curve, trades, industry, cost)
            infos[f"{cost}x"] = info
            if cost == 3:
                trades_3x = trades
        validation_trades = _period_trades(trades_3x, VALIDATION_START, END)
        validation_aggregate = r239._aggregate(validation_trades, industry)
        record = {
            "overrides": {"neutralize": VARIANTS[variant]["neutralize"]} if VARIANTS[variant]["neutralize"] else {},
            "runs": runs,
            "validation_positive_symbols": sum(row["pnl_amount"] > 0 for row in validation_aggregate["per_symbol"]),
        }
        if variant != "control":
            record["frozen_gate_result"] = _gate_delta(record, controls, controls["runs"]["3x"]["validation"])
        variant_results[variant] = record
        run_proofs[variant] = infos

    decision = variant_results["industry_shrink_05"]["frozen_gate_result"]["decision"]
    all_before_panel = all(
        value is True for key, value in decision.items() if key != "P2"
    )
    result = {
        "round": ROUND_ID,
        "classification": "exploratory_after_initial_result_exposure_not_independent_confirmation",
        "initial_preregistration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_preregistration.json",
        "initial_preregistration_sha256": prereg["initial_preregistration_sha256"],
        "execution_spec": EXECUTION_SPEC.relative_to(ROOT).as_posix(),
        "strategy": SID,
        "hypothesis": prereg["hypothesis"],
        "execution": prereg["execution"],
        "costs": {str(k): list(v) for k, v in COSTS.items()},
        "control_round": "R266",
        "control": controls,
        "variants": variant_results,
        "candidate_runs": run_proofs,
        "decision": {
            "decision": decision,
            "all_pass_before_panel": all_before_panel,
            "all_pass": False,
            "passing_variants": [],
            "production_state": "pause_production",
            "research_candidate_only_if_all_gates_pass": True,
        },
        "provenance": {
            "execution_spec_sha256": _proof(EXECUTION_SPEC)["sha256"],
            "runner_sha256": _proof(Path(__file__).resolve())["sha256"],
            "panel_registrar_sha256": _proof(ROOT / "scripts" / "register_r271_panel.py")["sha256"],
            "strategy_source": _proof(DATA / "strategies" / "ai" / f"{SID}.py"),
            "candidate_strategy_source": _proof(DATA / "strategies" / "ai" / f"{R271_SID}.py"),
            "historical_membership": _proof(r112.MEMBERSHIP),
            "strategy_backtest_service": _proof(ROOT / "backend" / "app" / "backtest" / "strategy.py"),
            "backtest_engine": _proof(ROOT / "backend" / "app" / "backtest" / "engine.py"),
            "scoring_neutralization": _proof(ROOT / "backend" / "app" / "strategy" / "scoring.py"),
            "industry_snapshot": _proof(DATA / "ext_data" / "ext_hy_ths" / "part.parquet"),
            "r266_controls": control_proofs,
        },
        "known_limitations": prereg["known_limitations"],
        "panel_check_pending": True,
        "panel_registration": "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r271_corrected_panel_registration.json",
        "all_pass": False,
        "passing_variants": [],
        "paper_trading_candidate": False,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "round": ROUND_ID,
        "variant": "industry_shrink_05",
        "full_return": variant_results["industry_shrink_05"]["runs"]["3x"]["full"]["total_return"],
        "sharpe": variant_results["industry_shrink_05"]["runs"]["3x"]["full"]["sharpe"],
        "mdd": variant_results["industry_shrink_05"]["runs"]["3x"]["full"]["mdd"],
        "gates": decision,
        "all_pass_before_panel": all_before_panel,
        "evidence": OUT.relative_to(ROOT).as_posix(),
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
