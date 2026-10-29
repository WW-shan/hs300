#!/usr/bin/env python3
"""Run frozen R275 single-gate ablations through the system backtest service."""
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
PREREG = EVIDENCE / "r275_preregistration_v3c.json"
PANEL = EVIDENCE / "r275_panel_registration_v3c.json"
R266 = EVIDENCE / "r266_a_leg_validation.json"
OUT = EVIDENCE / "r275_single_gate_ablation_v3c.json"
CACHE = DATA / "research/r275_cache_v3c"
START, END = date(2021, 6, 1), date(2026, 10, 16)
DISCOVERY_END, VALIDATION_START = date(2023, 12, 31), date(2024, 1, 1)
COSTS = {3: (0.0006, 0.0006, 0.001, 15.0), 4: (0.0008, 0.0008, 0.001333333333, 20.0), 5: (0.001, 0.001, 0.001666666667, 25.0)}
CANDIDATES = {
    "erp": "ai_hs300_macro_gate_quality_r275_remove_erp",
    "credit": "ai_hs300_macro_gate_quality_r275_remove_credit",
    "term": "ai_hs300_macro_gate_quality_r275_remove_term",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_artifact(path: Path, sidecar: Path, label: str) -> None:
    if not path.is_file() or not sidecar.is_file() or sidecar.read_text(encoding="ascii").split()[0] != _sha(path):
        raise RuntimeError(f"{label} missing or checksum mismatch")


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha(path).encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(paths), "inventory_sha256": digest.hexdigest()}


def _period_curve(curve: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    prior = curve.filter(pl.col("date") < start).sort("date").tail(1)
    selected = curve.filter(pl.col("date").is_between(start, end, closed="both")).sort("date")
    if prior.height:
        selected = pl.concat([prior, selected]).unique(subset=["date"], keep="last").sort("date")
    if selected.height < 2 or float(selected["value"][0]) <= 0:
        raise RuntimeError(f"insufficient curve data for {start}..{end}")
    base = float(selected["value"][0])
    return selected.with_columns((pl.col("value") / base * 1_000_000).alias("value"))


def _rolling(curve: pl.DataFrame) -> dict:
    values = curve["value"].to_list()
    returns = [values[i + 251] / values[i] - 1 for i in range(max(0, len(values) - 251))]
    return {"windows": len(returns), "positive_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0}


def _trade_summary(trades: list[dict]) -> dict:
    returns = [float(row["pnl_pct"]) for row in trades if row.get("pnl_pct") is not None]
    losses = [value for value in returns if value < 0]
    return {
        "trades": len(returns),
        "losses": len(losses),
        "mean_loss_magnitude_pct": -statistics.mean(losses) if losses else None,
        "worst_trade_return_pct": min(returns) if returns else None,
    }


def _period_trades(trades: list[dict]) -> list[dict]:
    return [row for row in trades if VALIDATION_START <= date.fromisoformat(str(row.get("entry_date"))[:10]) <= END]


def _run(service, membership: pl.DataFrame, r112, strategy_id: str, key: str, cost: int, prereg: dict, panel: dict, r266: dict) -> tuple[pl.DataFrame, list[dict], dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    stem = f"{strategy_id}_{cost}x_{START}_{END}"
    curve_path, trades_path, manifest_path = (CACHE / f"{stem}{suffix}" for suffix in (".parquet", ".trades.json", ".manifest.json"))
    paths = (curve_path, trades_path, manifest_path)
    if any(path.exists() for path in paths):
        if not all(path.exists() for path in paths):
            raise RuntimeError(f"partial R275 cache for {stem}; refusing reuse")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("round") != "R275" or manifest.get("preregistration_sha256") != _sha(PREREG) or manifest.get("panel_registration_sha256") != _sha(PANEL):
            raise RuntimeError(f"R275 cache provenance mismatch for {stem}")
        for label, digest in manifest.get("source_sha256", {}).items():
            if not (ROOT / label).is_file() or _sha(ROOT / label) != digest:
                raise RuntimeError(f"R275 cached source changed: {label}")
        if _sha(curve_path) != manifest.get("curve_sha256") or _sha(trades_path) != manifest.get("trades_sha256"):
            raise RuntimeError(f"R275 cache artifact hash mismatch for {stem}")
        return (pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date"), json.loads(trades_path.read_text(encoding="utf-8")), {"cached": True, "curve_sha256": _sha(curve_path), "trades_sha256": _sha(trades_path), "manifest_sha256": _sha(manifest_path), "stats": manifest["stats"]})

    fee, commission, stamp_tax, slippage = COSTS[cost]
    cfg = r112.StrategyBacktestConfig(
        strategy_id=strategy_id, symbols=None, start=START, end=END, params={}, overrides={},
        matching="open_t+1", fees_pct=fee, commission_pct=commission, stamp_tax_pct=stamp_tax,
        slippage_bps=slippage, max_positions=10, position_sizing="equal", mode="position",
        asset_type="stock", hs300=True,
    )
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"R275 {key} {cost}x failed: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [{name: trade.get(name) for name in ("symbol", "name", "entry_date", "exit_date", "pnl_pct", "pnl_amount", "exit_reason", "exit_signal_id")} for trade in result.trades]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cost_key = f"{cost}x"
    source_paths = {
        "scripts/backtest_r275_single_gate_ablation_v3c.py": Path(__file__).resolve(),
        "scripts/backtest_r112_dual_sleeve_risk_parity.py": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
        "backend/app/backtest/strategy.py": ROOT / "backend/app/backtest/strategy.py",
        "backend/app/backtest/engine.py": ROOT / "backend/app/backtest/engine.py",
        "backend/app/strategy/engine.py": ROOT / "backend/app/strategy/engine.py",
        f"data/strategies/ai/{strategy_id}.py": DATA / "strategies/ai" / f"{strategy_id}.py",
        "data/strategies/ai/ai_hs300_macro_gate_quality.py": DATA / "strategies/ai/ai_hs300_macro_gate_quality.py",
        "data/research/r78_daily_membership/daily_snapshots.parquet": r112.MEMBERSHIP,
        "data/ext_data/alt_hs300/config.json": DATA / "ext_data/alt_hs300/config.json",
    }
    manifest = {
        "round": "R275", "candidate": key, "strategy_id": strategy_id, "cost_multiple": cost,
        "window": [str(START), str(END)], "matching": "open_t+1", "membership": "r78_daily_membership",
        "max_positions": 10, "position_sizing": "equal", "max_hold_days": 35,
        "preregistration_sha256": _sha(PREREG), "panel_registration_sha256": _sha(PANEL),
        "control_curve_sha256": r266["runs"][cost_key]["curve_cache"]["sha256"],
        "control_trades_sha256": r266["runs"][cost_key]["trades_cache"]["sha256"],
        "candidate_source_sha256": prereg["candidates"][key]["source_sha256"],
        "source_sha256": {label: _sha(path) for label, path in source_paths.items()},
        "curve_sha256": _sha(curve_path), "trades_sha256": _sha(trades_path),
        "stats": result.stats, "factor_attribution": result.factor_attribution,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    info = {"cached": False, "curve_sha256": manifest["curve_sha256"], "trades_sha256": manifest["trades_sha256"], "manifest_sha256": _sha(manifest_path), "stats": result.stats}
    print(f"R275 {key} {cost}x: days={curve.height}, trades={len(trades)}", flush=True)
    gc.collect()
    return curve, trades, info


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R275 result exists; refusing overwrite")
    _validate_artifact(PREREG, PREREG.with_suffix(".sha256"), "R275 preregistration")
    _validate_artifact(PANEL, PANEL.with_suffix(".json.sha256"), "R275 panel proof")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if prereg.get("round") != "R275" or panel.get("all_checks_pass") is not True or panel.get("backtest_run") is not False:
        raise RuntimeError("R275 preregistration or panel gate failed")
    if prereg.get("runner_sha256") != _sha(Path(__file__).resolve()) or prereg.get("panel_registrar_sha256") != _sha(ROOT / "scripts/register_r275_panel_v3c.py"):
        raise RuntimeError("R275 runner/registrar changed after preregistration")
    if panel.get("preregistration_sha256") != _sha(PREREG):
        raise RuntimeError("R275 panel proof references a different freeze")
    for key, strategy_id in CANDIDATES.items():
        if _sha(DATA / "strategies/ai" / f"{strategy_id}.py") != prereg["candidates"][key]["source_sha256"]:
            raise RuntimeError(f"R275 {key} strategy changed after preregistration")
    for key, path in prereg["execution"]["system_source_sha256"].items():
        if _sha(ROOT / {
            "strategy_service": "backend/app/backtest/strategy.py",
            "backtest_engine": "backend/app/backtest/engine.py",
            "strategy_engine": "backend/app/strategy/engine.py",
            "indicator_pipeline": "backend/app/indicators/pipeline.py",
            "membership_runner": "scripts/backtest_r112_dual_sleeve_risk_parity.py",
            "concentration_audit": "scripts/backtest_r239_concentration_audit.py",
        }[key]) != path:
            raise RuntimeError(f"R275 system source changed after freeze: {key}")
    r266 = json.loads(R266.read_text(encoding="utf-8"))
    if _sha(R266) != prereg["execution"]["r266_evidence_sha256"]:
        raise RuntimeError("R266 finalized evidence changed")
    control_paths = {}
    for cost in COSTS:
        control_paths[cost] = {}
        for kind in ("curve", "trades"):
            proof = r266["runs"][f"{cost}x"][f"{kind}_cache"]
            path = ROOT / proof["path"]
            expected = prereg["execution"]["r266_control_cache_hashes"][f"{cost}x"][kind]
            if _sha(path) != proof["sha256"] or proof["sha256"] != expected:
                raise RuntimeError(f"R266 {cost}x {kind} parity failed")
            control_paths[cost][kind] = path
    membership_path = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    if _sha(membership_path) != prereg["execution"]["historical_membership_sha256"]:
        raise RuntimeError("historical membership changed after freeze")
    macro_snapshot = {"config_sha256": _sha(DATA / "ext_data/alt_hs300/config.json"), **_inventory(DATA / "ext_data/alt_hs300/timeseries")}
    if macro_snapshot != prereg["execution"]["macro_data"]:
        raise RuntimeError("macro data changed after freeze")
    r112 = _load(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r275")
    r239 = _load(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r275")
    from app.services.industry_map import load_industry_l1

    membership = r112._historical_membership()
    service = r112._service()
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row.get("industry_l1")}
    control_curves, control_trades = {}, {}
    for cost in COSTS:
        control_curves[cost] = pl.read_parquet(control_paths[cost]["curve"]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        control_trades[cost] = json.loads(control_paths[cost]["trades"].read_text(encoding="utf-8"))
    candidates = {}
    for key, strategy_id in CANDIDATES.items():
        curves, trades_by_cost, runs = {}, {}, {}
        for cost in COSTS:
            curve, trades, info = _run(service, membership, r112, strategy_id, key, cost, prereg, panel, r266)
            curves[cost], trades_by_cost[cost], runs[f"{cost}x"] = curve, trades, info
        full = r112._stats(curves[3])
        discovery = r112._stats(_period_curve(curves[3], START, DISCOVERY_END))
        validation = r112._stats(_period_curve(curves[3], VALIDATION_START, END))
        control_full = r266["full"]
        control_validation = r112._stats(_period_curve(control_curves[3], VALIDATION_START, END))
        validation_trades = _period_trades(trades_by_cost[3])
        control_validation_trades = _period_trades(control_trades[3])
        concentration = r239._aggregate(trades_by_cost[3], industry)
        validation_concentration = r239._aggregate(validation_trades, industry)
        control_concentration = r239._aggregate(control_trades[3], industry)
        control_loss, candidate_loss = _trade_summary(control_trades[3]), _trade_summary(trades_by_cost[3])
        control_validation_loss, candidate_validation_loss = _trade_summary(control_validation_trades), _trade_summary(validation_trades)
        rolling = _rolling(curves[3])
        validation_positive_symbols = sum(row["pnl_amount"] > 0 for row in validation_concentration["per_symbol"])
        def within(value: float | None, control_value: float | None, allowance: float) -> bool:
            return value is not None and control_value is not None and value <= control_value + allowance
        decisions = {
            "P1": True,
            "P2": panel.get("all_checks_pass") is True,
            "G1": full["total_return"] > 0 and full["total_return"] >= control_full["total_return"] * 0.70,
            "G2": full["mdd"] >= -0.25,
            "G3": rolling["positive_rate"] >= 0.70,
            "G4": validation["total_return"] > 0 and validation["annual"] >= 0.05,
            "G5": validation["total_return"] >= control_validation["total_return"] * 0.70,
            "G6": all(r112._stats(curves[cost])["total_return"] > 0 for cost in (4, 5)),
            "R1": full["sharpe"] >= control_full["sharpe"],
            "R2": full["mdd"] >= control_full["mdd"] - 0.03,
            "L1": within(candidate_loss["mean_loss_magnitude_pct"], control_loss["mean_loss_magnitude_pct"], 0.01),
            "L2": within(candidate_loss["worst_trade_return_pct"], control_loss["worst_trade_return_pct"], 0.05),
            "C1": concentration["distinct_symbols"] >= 30,
            "C2": concentration["total_pnl_amount"] > 0 and concentration["top1_net_share"] <= 0.25,
            "C3": concentration["total_pnl_amount"] > 0 and concentration["top3_net_share"] <= 0.55,
            "C4": concentration["total_pnl_amount"] > 0 and concentration["top5_net_share"] <= 0.75,
            "C5": validation_positive_symbols >= 8,
            "C6": concentration["top_industry_gross_share"] is not None and concentration["top_industry_gross_share"] <= 0.45,
            "C7": concentration["max_symbol_loss_to_gross"] is not None and concentration["max_symbol_loss_to_gross"] <= 0.20,
            "C8": validation_concentration["total_pnl_amount"] > 0 and validation_concentration["top1_net_share"] <= 0.35,
        }
        candidates[key] = {
            "strategy_id": strategy_id, "full": full, "discovery": discovery, "validation": validation,
            "rolling_1y": rolling, "trade_loss_full": candidate_loss, "trade_loss_validation_entry": candidate_validation_loss,
            "concentration_full": {k: v for k, v in concentration.items() if k != "per_symbol"},
            "concentration_validation_entry": {k: v for k, v in validation_concentration.items() if k != "per_symbol"},
            "validation_positive_symbols": validation_positive_symbols,
            "control": {"full": control_full, "validation": control_validation, "trade_loss_full": control_loss, "trade_loss_validation_entry": control_validation_loss, "concentration_full": {k: v for k, v in control_concentration.items() if k != "per_symbol"}},
            "comparison": {"full_return_retention": full["total_return"] / control_full["total_return"], "validation_return_retention": validation["total_return"] / control_validation["total_return"]},
            "runs": runs, "decision": decisions, "all_pass": all(decisions.values()),
        }
        print(f"R275 {key}: full={full['total_return']:.4%}, validation={validation['total_return']:.4%}, all_pass={all(decisions.values())}", flush=True)
    payload = {
        "round": "R275", "status": "complete",
        "classification": prereg["classification"],
        "preregistration_sha256": _sha(PREREG), "panel_registration_sha256": _sha(PANEL),
        "execution": {"service": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "window": [str(START), str(END)], "membership": "r78_daily_membership", "max_positions": 10, "position_sizing": "equal", "max_hold_days": 35, "only_changed_behavior": "remove one specified macro entry gate per candidate"},
        "candidates": candidates,
        "family_passers": [key for key, value in candidates.items() if value["all_pass"]],
        "family_decision": "all_failed_research_only" if not any(value["all_pass"] for value in candidates.values()) else "passing_variants_require_new_untouched_holdout_before_any_promotion",
        "production_status": "pause_production", "limitations": prereg["known_limitations"],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    digest = _sha(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"result": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "family_passers": payload["family_passers"], "decisions": {key: value["decision"] for key, value in candidates.items()}}, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload["family_passers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
