#!/usr/bin/env python3
"""Run the frozen R274 macro-entry-gate ablation through the system service."""
from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import statistics
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r274_preregistration.json"
PANEL = EVIDENCE / "r274_panel_registration.json"
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
OUT = EVIDENCE / "r274_no_macro_gate.json"
OUT_HASH = EVIDENCE / "r274_no_macro_gate.json.sha256"
CACHE = DATA / "research/r274_cache"
STRATEGY = "ai_hs300_macro_gate_quality_r274_no_macro_gate"
START, END = date(2021, 6, 1), date(2026, 10, 16)
DISCOVERY_END, VALIDATION_START = date(2023, 12, 31), date(2024, 1, 1)
COSTS = {
    3: (0.0006, 0.0006, 0.001, 15.0),
    4: (0.0008, 0.0008, 0.001333333333, 20.0),
    5: (0.001, 0.001, 0.001666666667, 25.0),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_sidecar(artifact: Path, sidecar: Path, label: str) -> None:
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != _sha256(artifact):
        raise RuntimeError(f"{label} checksum mismatch or missing sidecar")


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        size += path.stat().st_size
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(paths), "total_size_bytes": size, "inventory_sha256": digest.hexdigest()}


def _period_curve(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    prior = curve.filter(pl.col("date") < start).sort("date")
    selected = curve.filter(pl.col("date").is_between(start, end, closed="both")).sort("date")
    if prior.height:
        selected = pl.concat([prior.tail(1), selected]).unique(subset=["date"], keep="last").sort("date")
    if selected.height < 2:
        raise RuntimeError(f"insufficient curve data for {start}..{end}")
    base = float(selected["value"][0])
    if base <= 0:
        raise RuntimeError("invalid prior-close rebasing base")
    return selected.with_columns((pl.col("value") / base * 1_000_000.0).alias("value"))


def _rolling(curve: pl.DataFrame, window: int = 252) -> dict:
    values = curve["value"].to_list()
    returns = [values[i + window - 1] / values[i] - 1.0 for i in range(max(0, len(values) - window + 1))]
    return {
        "windows": len(returns),
        "positive_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        "return_worst": min(returns) if returns else None,
        "return_median": statistics.median(returns) if returns else None,
    }


def _loss_summary(trades: list[dict]) -> dict:
    returns = [float(row["pnl_pct"]) for row in trades if row.get("pnl_pct") is not None]
    losses = [value for value in returns if value < 0]
    mean_loss = statistics.mean(losses) if losses else None
    return {
        "trades": len(returns),
        "losses": len(losses),
        "mean_loss_pct": round(mean_loss, 6) if mean_loss is not None else None,
        "mean_loss_magnitude_pct": round(-mean_loss, 6) if mean_loss is not None else None,
        "worst_trade_return_pct": min(returns) if returns else None,
        "open_at_window_end_trades": sum(row.get("exit_reason") == "end" for row in trades),
    }


def _period_trades(trades: list[dict], start: date | None = None, end: date | None = None) -> list[dict]:
    selected = []
    for row in trades:
        raw = str(row.get("entry_date", ""))[:10]
        if len(raw) != 10:
            continue
        day = date.fromisoformat(raw)
        if (start is not None and day < start) or (end is not None and day > end):
            continue
        selected.append(row)
    return selected


def _run(service, membership: pl.DataFrame, r112, cost: int) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{STRATEGY}_{cost}x_{START}_{END}"
    curve_path = CACHE / f"{key}.parquet"
    trades_path = CACHE / f"{key}.trades.json"
    manifest_path = CACHE / f"{key}.manifest.json"
    paths = (curve_path, trades_path, manifest_path)
    if any(path.exists() for path in paths):
        if not all(path.exists() for path in paths):
            raise RuntimeError(f"partial R274 cache exists for {key}; refusing reuse")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("round") != "R274" or manifest.get("preregistration_sha256") != _sha256(PREREG) or manifest.get("panel_registration_sha256") != _sha256(PANEL):
            raise RuntimeError(f"R274 cache provenance mismatch for {key}")
        for label, digest in manifest.get("source_sha256", {}).items():
            path = ROOT / label
            if not path.is_file() or _sha256(path) != digest:
                raise RuntimeError(f"R274 cached source changed: {label}")
        if manifest.get("curve_sha256") != _sha256(curve_path) or manifest.get("trades_sha256") != _sha256(trades_path):
            raise RuntimeError(f"R274 cache artifact hash mismatch for {key}")
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return curve, trades, {"cached": True, "curve_sha256": _sha256(curve_path), "trades_sha256": _sha256(trades_path), "manifest_sha256": _sha256(manifest_path), "stats": manifest["stats"]}

    fee, commission, stamp_tax, slippage = COSTS[cost]
    cfg = r112.StrategyBacktestConfig(
        strategy_id=STRATEGY, symbols=None, start=START, end=END, params={}, overrides={},
        matching="open_t+1", fees_pct=fee, commission_pct=commission,
        stamp_tax_pct=stamp_tax, slippage_bps=slippage, max_positions=10,
        position_sizing="equal", mode="position", asset_type="stock", hs300=True,
    )
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"R274 {cost}x system backtest failed: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [{key: trade.get(key) for key in (
        "symbol", "name", "entry_date", "exit_date", "pnl_pct", "pnl_amount",
        "exit_reason", "exit_signal_date", "exit_signal_id", "blocked_exit_days",
    )} for trade in result.trades]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    control = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    cost_key = f"{cost}x"
    sources = {
        "scripts/backtest_r274_no_macro_gate.py": _sha256(Path(__file__).resolve()),
        "scripts/backtest_r112_dual_sleeve_risk_parity.py": _sha256(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py"),
        "backend/app/backtest/strategy.py": _sha256(ROOT / "backend/app/backtest/strategy.py"),
        "backend/app/backtest/engine.py": _sha256(ROOT / "backend/app/backtest/engine.py"),
        "backend/app/strategy/engine.py": _sha256(ROOT / "backend/app/strategy/engine.py"),
        f"data/strategies/ai/{STRATEGY}.py": _sha256(DATA / "strategies/ai" / f"{STRATEGY}.py"),
        "data/strategies/ai/ai_hs300_macro_gate_quality.py": _sha256(DATA / "strategies/ai/ai_hs300_macro_gate_quality.py"),
        "data/research/r78_daily_membership/daily_snapshots.parquet": _sha256(r112.MEMBERSHIP),
        "data/ext_data/alt_hs300/config.json": _sha256(DATA / "ext_data/alt_hs300/config.json"),
    }
    manifest = {
        "round": "R274", "strategy_id": STRATEGY, "cost_multiple": cost,
        "window": [str(START), str(END)], "matching": "open_t+1",
        "max_positions": 10, "position_sizing": "equal",
        "preregistration_sha256": _sha256(PREREG), "panel_registration_sha256": _sha256(PANEL),
        "control_curve_sha256": control["runs"][cost_key]["curve_cache"]["sha256"],
        "control_trades_sha256": control["runs"][cost_key]["trades_cache"]["sha256"],
        "source_sha256": sources, "curve_sha256": _sha256(curve_path),
        "trades_sha256": _sha256(trades_path), "stats": result.stats,
        "factor_attribution": result.factor_attribution,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    info = {"cached": False, "curve_sha256": manifest["curve_sha256"], "trades_sha256": manifest["trades_sha256"], "manifest_sha256": _sha256(manifest_path), "stats": result.stats}
    print(f"R274 {cost}x: days={curve.height}, trades={len(trades)}", flush=True)
    gc.collect()
    return curve, trades, info


def _stats(curve: pl.DataFrame, r112) -> dict:
    return r112._stats(curve)


def main() -> int:
    if OUT.exists() or OUT_HASH.exists():
        raise RuntimeError("R274 result evidence exists; refusing overwrite")
    for path, sidecar, label in ((PREREG, PREREG.with_suffix(".sha256"), "R274 preregistration"), (PANEL, PANEL.with_suffix(".json.sha256"), "R274 panel proof")):
        if not path.is_file():
            raise RuntimeError(f"{label} is required")
        _check_sidecar(path, sidecar, label)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if prereg.get("round") != "R274" or prereg.get("status") != "pre_registered_before_panel_registration_and_backtest":
        raise RuntimeError("R274 preregistration status mismatch")
    if panel.get("all_checks_pass") is not True or panel.get("backtest_run") is not False:
        raise RuntimeError("R274 panel gate failed or records a prior backtest")
    if panel.get("preregistration", {}).get("sha256") != _sha256(PREREG):
        raise RuntimeError("R274 panel proof references another preregistration")
    if prereg.get("runner_sha256") != _sha256(Path(__file__).resolve()) or prereg.get("panel_registrar_sha256") != _sha256(ROOT / "scripts/register_r274_panel.py"):
        raise RuntimeError("R274 runner or panel registrar changed after preregistration")
    if prereg["execution"]["candidate_source_sha256"] != _sha256(DATA / "strategies/ai" / f"{STRATEGY}.py"):
        raise RuntimeError("R274 candidate strategy changed after freeze")
    control = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    if _sha256(R266_RESULT) != prereg["execution"]["r266_evidence_sha256"]:
        raise RuntimeError("R266 finalized evidence changed")
    control_paths = {}
    for cost in COSTS:
        control_paths[cost] = {}
        for kind in ("curve", "trades"):
            proof = control["runs"][f"{cost}x"][f"{kind}_cache"]
            path = ROOT / proof["path"]
            if not path.is_file() or _sha256(path) != proof["sha256"] or prereg["execution"]["r266_control_cache_hashes"][f"{cost}x"][kind] != proof["sha256"]:
                raise RuntimeError(f"R266 {cost}x {kind} cache failed frozen hash check")
            control_paths[cost][kind] = path
    membership_path = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    if _sha256(membership_path) != prereg["execution"]["historical_membership_sha256"]:
        raise RuntimeError("historical membership changed after preregistration")
    macro_snapshot = {"config_sha256": _sha256(DATA / "ext_data/alt_hs300/config.json"), **_inventory(DATA / "ext_data/alt_hs300/timeseries")}
    if macro_snapshot != prereg["execution"]["macro_data"]:
        raise RuntimeError("macro data changed after preregistration")
    source_paths = {
        "strategy_service": ROOT / "backend/app/backtest/strategy.py",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_engine": ROOT / "backend/app/strategy/engine.py",
        "indicator_pipeline": ROOT / "backend/app/indicators/pipeline.py",
        "membership_runner": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    }
    if any(_sha256(path) != prereg["execution"]["system_source_sha256"][name] for name, path in source_paths.items()):
        raise RuntimeError("R274 system source changed after preregistration")
    if _sha256(DATA / "strategies/ai/ai_hs300_macro_gate_quality.py") != prereg["execution"]["control_source_sha256"]:
        raise RuntimeError("R266 control strategy changed after preregistration")
    r112 = _load(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r274")
    r239 = _load(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r274")
    from app.services.industry_map import load_industry_l1

    membership = r112._historical_membership()
    service = r112._service()
    curves, trades_by_cost, runs = {}, {}, {}
    control_curves, control_trades = {}, {}
    for cost in COSTS:
        curve, trades, info = _run(service, membership, r112, cost)
        curves[cost], trades_by_cost[cost], runs[f"{cost}x"] = curve, trades, info
        control_curves[cost] = pl.read_parquet(control_paths[cost]["curve"]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        control_trades[cost] = json.loads(control_paths[cost]["trades"].read_text(encoding="utf-8"))
    candidate_full = _stats(curves[3], r112)
    candidate_validation_curve = _period_curve(curves[3], VALIDATION_START, END)
    candidate_discovery_curve = _period_curve(curves[3], START, DISCOVERY_END)
    control_full = control["full"]
    control_validation = _stats(_period_curve(control_curves[3], VALIDATION_START, END), r112)
    candidate_validation = _stats(candidate_validation_curve, r112)
    candidate_discovery = _stats(candidate_discovery_curve, r112)
    candidate_validation_trades = _period_trades(trades_by_cost[3], VALIDATION_START, END)
    control_validation_trades = _period_trades(control_trades[3], VALIDATION_START, END)
    candidate_loss_full = _loss_summary(trades_by_cost[3])
    control_loss_full = _loss_summary(control_trades[3])
    candidate_loss_validation = _loss_summary(candidate_validation_trades)
    control_loss_validation = _loss_summary(control_validation_trades)
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row.get("industry_l1")}
    concentration = r239._aggregate(trades_by_cost[3], industry)
    validation_concentration = r239._aggregate(candidate_validation_trades, industry)
    control_concentration = r239._aggregate(control_trades[3], industry)
    validation_positive_symbols = sum(row["pnl_amount"] > 0 for row in validation_concentration["per_symbol"])
    rolling = _rolling(curves[3])
    def share(value: float | None, control_value: float | None) -> bool:
        return value is not None and control_value is not None and value <= control_value - 0.01
    decisions = {
        "P1": True,
        "P2": panel.get("all_checks_pass") is True,
        "G1": candidate_full["total_return"] > 0 and candidate_full["total_return"] >= control_full["total_return"] * 0.70,
        "G2": candidate_full["mdd"] >= -0.25,
        "G3": rolling["positive_rate"] >= 0.70,
        "G4": candidate_validation["total_return"] > 0,
        "G5": candidate_validation["annual"] >= 0.05,
        "G6": _stats(curves[4], r112)["total_return"] > 0,
        "G7": _stats(curves[5], r112)["total_return"] > 0,
        "G8": candidate_validation["total_return"] >= control_validation["total_return"] * 0.70,
        "R1": candidate_full["sharpe"] >= control_full["sharpe"],
        "R2": candidate_full["mdd"] >= control_full["mdd"] - 0.03,
        "L1": share(candidate_loss_full["mean_loss_magnitude_pct"], control_loss_full["mean_loss_magnitude_pct"]),
        "L2": share(candidate_loss_validation["mean_loss_magnitude_pct"], control_loss_validation["mean_loss_magnitude_pct"]),
        "L3": candidate_loss_full["worst_trade_return_pct"] is not None and control_loss_full["worst_trade_return_pct"] is not None and candidate_loss_full["worst_trade_return_pct"] > control_loss_full["worst_trade_return_pct"],
        "L4": candidate_loss_validation["worst_trade_return_pct"] is not None and control_loss_validation["worst_trade_return_pct"] is not None and candidate_loss_validation["worst_trade_return_pct"] > control_loss_validation["worst_trade_return_pct"],
        "C1": concentration["distinct_symbols"] >= 30,
        "C2": concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25,
        "C3": concentration["total_pnl_amount"] > 0 and concentration["top3_net_share"] <= 0.55,
        "C4": concentration["total_pnl_amount"] > 0 and concentration["top5_net_share"] <= 0.75,
        "C5": validation_positive_symbols >= 8,
        "C6": concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45,
        "C7": concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20,
        "C8": validation_concentration["total_pnl_amount"] > 0 and validation_concentration["top1_net_share"] <= 0.35,
    }
    payload = {
        "round": "R274", "status": "complete",
        "classification": "single_variant_exploratory_ablation_not_independent_confirmation",
        "preregistration": PREREG.relative_to(ROOT).as_posix(), "preregistration_sha256": _sha256(PREREG),
        "panel_registration": PANEL.relative_to(ROOT).as_posix(), "panel_registration_sha256": _sha256(PANEL),
        "execution": {"service": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "window": [str(START), str(END)], "membership": "r78_daily_membership", "max_positions": 10, "position_sizing": "equal", "max_hold_days": 35, "only_changed_behavior": "remove three macro entry gates; no new exit rule"},
        "runs": runs,
        "candidate": {"full": candidate_full, "discovery": candidate_discovery, "validation": candidate_validation, "rolling_1y": rolling, "trade_loss_full": candidate_loss_full, "trade_loss_validation_entry": candidate_loss_validation, "concentration_full": {k:v for k,v in concentration.items() if k != "per_symbol"}, "concentration_validation_entry": {k:v for k,v in validation_concentration.items() if k != "per_symbol"}, "validation_positive_symbols": validation_positive_symbols},
        "control": {"full": control_full, "validation": control_validation, "trade_loss_full": control_loss_full, "trade_loss_validation_entry": control_loss_validation, "concentration_full": {k:v for k,v in control_concentration.items() if k != "per_symbol"}},
        "comparison": {"full_return_retention": candidate_full["total_return"] / control_full["total_return"] if control_full["total_return"] else None, "validation_return_retention": candidate_validation["total_return"] / control_validation["total_return"] if control_validation["total_return"] else None, "prior_close_rebased_validation": True},
        "decision": decisions, "all_pass": all(decisions.values()),
        "research_decision": "eligible_for_separate_paper_review_only" if all(decisions.values()) else "research_only_rejected",
        "production_status": "pause_production",
        "limitations": prereg["known_limitations"],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT_HASH.write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"result": OUT.relative_to(ROOT).as_posix(), "all_pass": payload["all_pass"], "decision": decisions, "candidate_full": candidate_full, "candidate_validation": candidate_validation, "control_full": control_full, "control_validation": control_validation, "sha256": digest}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0 if payload["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
