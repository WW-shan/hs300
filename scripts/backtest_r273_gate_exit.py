#!/usr/bin/env python3
"""Run the frozen R273 gate-exit candidate through the system backtest service."""
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
PREREG = EVIDENCE / "r273_preregistration_v4.json"
PREREG_HASH = PREREG.with_suffix(".sha256")
PANEL = EVIDENCE / "r273_panel_registration_v2.json"
PANEL_HASH = PANEL.with_suffix(".json.sha256")
R266_RESULT = EVIDENCE / "r266_a_leg_validation.json"
OUT = EVIDENCE / "r273_gate_exit.json"
OUT_HASH = EVIDENCE / "r273_gate_exit.json.sha256"
CACHE = DATA / "research/r273_cache"
STRATEGY = "ai_hs300_macro_gate_quality_r273_gate_exit"
CONTROL = "ai_hs300_macro_gate_quality"
START, END = date(2021, 6, 1), date(2026, 10, 16)
DISCOVERY_END, VALIDATION_START = date(2023, 12, 31), date(2024, 1, 1)
SIGNAL_IDS = ["r273_macro_exit_erp", "r273_macro_exit_credit", "r273_macro_exit_term"]
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


def _check_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or _sha256(path) != expected:
        raise RuntimeError(f"{label} is missing or its SHA-256 differs from frozen evidence")


def _check_sidecar(artifact: Path, sidecar: Path, label: str) -> None:
    if not sidecar.is_file():
        raise RuntimeError(f"{label} checksum file is missing")
    parts = sidecar.read_text(encoding="ascii").split()
    if not parts or parts[0] != _sha256(artifact):
        raise RuntimeError(f"{label} checksum file does not match the artifact")


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        size += path.stat().st_size
        digest.update(relative.encode("utf-8"))
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
    first = float(selected["value"][0])
    if first <= 0:
        raise RuntimeError("invalid prior-close rebasing base")
    return selected.with_columns((pl.col("value") / first * 1_000_000.0).alias("value"))


def _stats(curve: pl.DataFrame, r112) -> dict:
    return r112._stats(curve)


def _rolling(curve: pl.DataFrame, window: int = 252) -> dict:
    values = curve["value"].to_list()
    compounded = [values[i + window - 1] / values[i] - 1.0 for i in range(max(0, len(values) - window + 1))]
    return {
        "windows": len(compounded),
        "positive_rate": sum(value > 0 for value in compounded) / len(compounded) if compounded else 0.0,
        "return_worst": min(compounded) if compounded else None,
        "return_median": statistics.median(compounded) if compounded else None,
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
    curve_path, trades_path, manifest_path = (CACHE / f"{key}.parquet", CACHE / f"{key}.trades.json", CACHE / f"{key}.manifest.json")
    cache_paths = (curve_path, trades_path, manifest_path)
    if any(path.exists() for path in cache_paths):
        if not all(path.exists() for path in cache_paths):
            raise RuntimeError(f"partial R273 cache exists for {key}; refusing reuse")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        control_evidence = json.loads(R266_RESULT.read_text(encoding="utf-8"))
        expected_control = control_evidence["runs"][f"{cost}x"]
        if (
            manifest.get("round") != "R273"
            or manifest.get("strategy_id") != STRATEGY
            or manifest.get("cost_multiple") != cost
            or manifest.get("preregistration_sha256") != _sha256(PREREG)
            or manifest.get("panel_registration_sha256") != _sha256(PANEL)
            or manifest.get("control_curve_sha256") != expected_control["curve_cache"]["sha256"]
            or manifest.get("control_trades_sha256") != expected_control["trades_cache"]["sha256"]
        ):
            raise RuntimeError(f"R273 cached run does not match frozen execution for {key}")
        for label, value in manifest.get("source_sha256", {}).items():
            source = ROOT / label
            if not source.is_file() or _sha256(source) != value:
                raise RuntimeError(f"R273 cached run source mismatch: {label}")
        if manifest.get("curve_sha256") != _sha256(curve_path) or manifest.get("trades_sha256") != _sha256(trades_path):
            raise RuntimeError(f"R273 cache artifact checksum mismatch for {key}")
        curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        trades = json.loads(trades_path.read_text(encoding="utf-8"))
        return curve, trades, {"cached": True, "curve_sha256": _sha256(curve_path), "trades_sha256": _sha256(trades_path), "manifest_sha256": _sha256(manifest_path), "stats": manifest["stats"]}

    fee, commission, stamp_tax, slippage = COSTS[cost]
    cfg = r112.StrategyBacktestConfig(
        strategy_id=STRATEGY,
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
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    with r112._patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"R273 {cost}x system backtest failed: {result.error}")
    curve = r112._curve_frame(result.equity_curve)
    trades = [{key: trade.get(key) for key in (
        "symbol", "name", "entry_date", "exit_date", "pnl_pct", "pnl_amount",
        "exit_reason", "exit_signal_date", "exit_signal_id", "blocked_exit_days",
    )} for trade in result.trades]
    curve.write_parquet(curve_path)
    trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    sources = {
        "scripts/backtest_r273_gate_exit.py": _sha256(Path(__file__).resolve()),
        "scripts/backtest_r112_dual_sleeve_risk_parity.py": _sha256(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py"),
        "backend/app/backtest/strategy.py": _sha256(ROOT / "backend/app/backtest/strategy.py"),
        "backend/app/backtest/engine.py": _sha256(ROOT / "backend/app/backtest/engine.py"),
        "backend/app/strategy/custom_signals.py": _sha256(ROOT / "backend/app/strategy/custom_signals.py"),
        "backend/app/indicators/pipeline.py": _sha256(ROOT / "backend/app/indicators/pipeline.py"),
        f"data/strategies/ai/{STRATEGY}.py": _sha256(DATA / "strategies/ai" / f"{STRATEGY}.py"),
        "data/research/r78_daily_membership/daily_snapshots.parquet": _sha256(r112.MEMBERSHIP),
        "data/ext_data/alt_hs300/config.json": _sha256(DATA / "ext_data/alt_hs300/config.json"),
    }
    sources.update({
        f"data/user_data/custom_signals/{sid}.json": _sha256(DATA / "user_data/custom_signals" / f"{sid}.json")
        for sid in SIGNAL_IDS
    })
    manifest = {
        "round": "R273",
        "strategy_id": STRATEGY,
        "cost_multiple": cost,
        "window": [str(START), str(END)],
        "matching": "open_t+1",
        "max_positions": 10,
        "position_sizing": "equal",
        "exit_signals": SIGNAL_IDS,
        "preregistration_sha256": _sha256(PREREG),
        "panel_registration_sha256": _sha256(PANEL),
        "control_curve_sha256": json.loads(R266_RESULT.read_text(encoding="utf-8"))["runs"][f"{cost}x"]["curve_cache"]["sha256"],
        "control_trades_sha256": json.loads(R266_RESULT.read_text(encoding="utf-8"))["runs"][f"{cost}x"]["trades_cache"]["sha256"],
        "source_sha256": sources,
        "curve_sha256": _sha256(curve_path),
        "trades_sha256": _sha256(trades_path),
        "stats": result.stats,
        "factor_attribution": result.factor_attribution,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    info = {"cached": False, "curve_sha256": manifest["curve_sha256"], "trades_sha256": manifest["trades_sha256"], "manifest_sha256": _sha256(manifest_path), "stats": result.stats}
    print(f"R273 {cost}x: days={curve.height}, trades={len(trades)}, exits={sum(bool(t.get('exit_signal_id')) for t in trades)}", flush=True)
    gc.collect()
    return curve, trades, info


def _daily_gate_diagnostics() -> dict:
    root = DATA / "ext_data/alt_hs300/timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("R273 macro factor timeseries is missing")
    frame = (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .filter(pl.col("date").is_between(START, END, closed="both"))
        .select(["date", "equity_risk_premium", "credit_spread_5y_low", "term_spread_10y_2y"])
        .unique()
        .sort("date")
        .collect()
    )
    gates = pl.any_horizontal(
        pl.col("equity_risk_premium") < 5.5,
        pl.col("credit_spread_5y_low") < -0.8,
        pl.col("term_spread_10y_2y") < 0.3,
    ).fill_null(False)
    by_day = frame.with_columns(gates.alias("closed"))
    rows = by_day.select(["date", "closed"]).to_dicts()
    closed_dates = {row["date"] for row in rows if row["closed"]}
    opened, closed = 0, 0
    previous = None
    for row in rows:
        state = bool(row["closed"])
        if previous is not None and state != previous:
            closed += int(state)
            opened += int(not state)
        previous = state
    return {
        "dates": frame.height,
        "closed_days": len(closed_dates),
        "open_days": frame.height - len(closed_dates),
        "transitions_open_to_closed": closed,
        "transitions_closed_to_open": opened,
        "first_date": str(frame["date"].min()),
        "last_date": str(frame["date"].max()),
        "all_three_inputs_market_level_daily_series": True,
    }


def main() -> int:
    if OUT.exists() or OUT_HASH.exists():
        raise RuntimeError("R273 result evidence exists; refusing to overwrite")
    if not PREREG.exists() or not PREREG_HASH.exists() or not PANEL.exists() or not PANEL_HASH.exists():
        raise RuntimeError("R273 preregistration and successful panel registration proof are required")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("round") != "R273" or prereg.get("status") != "pre_registered_after_setup_evidence_correction_before_backtest":
        raise RuntimeError("R273 preregistration status mismatch")
    prereg_digest = _sha256(PREREG)
    _check_sidecar(PREREG, PREREG_HASH, "R273 preregistration")
    if prereg.get("runner_sha256") != _sha256(Path(__file__).resolve()):
        raise RuntimeError("R273 runner changed after preregistration")
    if prereg.get("panel_registrar_sha256") != _sha256(ROOT / "scripts/register_r273_panel.py"):
        raise RuntimeError("R273 registrar changed after v2 preregistration")
    registrar = ROOT / "scripts/register_r273_panel.py"
    if prereg.get("panel_registrar_sha256") != _sha256(registrar):
        raise RuntimeError("R273 registrar changed after preregistration")
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    if panel.get("all_checks_pass") is not True or panel.get("backtest_run") is not False:
        raise RuntimeError("R273 panel registration checks failed or evidence already records a backtest")
    _check_sidecar(PANEL, PANEL_HASH, "R273 panel evidence")
    if panel.get("preregistration", {}).get("sha256") != prereg_digest:
        raise RuntimeError("R273 panel evidence refers to a different preregistration")
    if prereg.get("execution", {}).get("strategy_source_sha256") != _sha256(DATA / "strategies/ai" / f"{STRATEGY}.py"):
        raise RuntimeError("R273 candidate source changed after preregistration")
    expected_signals = prereg["execution"]["signal_sources"]
    for relative, digest in expected_signals.items():
        _check_hash(ROOT / relative, digest, f"frozen signal {relative}")

    r266 = json.loads(R266_RESULT.read_text(encoding="utf-8"))
    if r266.get("decision", {}).get("P1") is not True or r266.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 control parity and panel gates must pass")
    control_paths = {}
    for cost in COSTS:
        control_paths[str(cost)] = {}
        for kind in ("curve", "trades"):
            proof = r266["runs"][f"{cost}x"][f"{kind}_cache"]
            path = ROOT / proof["path"]
            _check_hash(path, proof["sha256"], f"R266 {cost}x {kind} cache")
            if proof["sha256"] != prereg["execution"]["control_cache_hashes"][f"{cost}x"][kind]:
                raise RuntimeError(f"R266 {cost}x {kind} cache differs from pre-registration")
            control_paths[str(cost)][kind] = path
    if prereg["execution"]["control_evidence_sha256"] != _sha256(R266_RESULT):
        raise RuntimeError("R266 finalized result evidence changed")
    membership_path = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    if prereg["execution"]["historical_membership_sha256"] != _sha256(membership_path):
        raise RuntimeError("historical HS300 membership changed after preregistration")
    if prereg["execution"]["macro_data"] != {
        "config_sha256": _sha256(DATA / "ext_data/alt_hs300/config.json"),
        **_inventory(DATA / "ext_data/alt_hs300/timeseries"),
    }:
        raise RuntimeError("macro factor inventory changed after preregistration")
    if prereg["execution"]["macro_data"] != panel.get("macro_data_inventory"):
        raise RuntimeError("panel registration does not record the frozen macro data inventory")
    source_paths = {
        "strategy_service": ROOT / "backend/app/backtest/strategy.py",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_engine": ROOT / "backend/app/strategy/engine.py",
        "custom_signals": ROOT / "backend/app/strategy/custom_signals.py",
        "indicator_pipeline": ROOT / "backend/app/indicators/pipeline.py",
        "historical_membership_source": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    }
    if any(_sha256(path) != prereg["execution"]["system_source_sha256"][name] for name, path in source_paths.items()):
        raise RuntimeError("system execution source changed after preregistration")
    if _sha256(DATA / "strategies/ai/ai_hs300_macro_gate_quality.py") != prereg["execution"]["control_strategy_sha256"]:
        raise RuntimeError("R266 control strategy source changed")

    r112 = _load(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r273")
    r239 = _load(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r273")
    from app.services.industry_map import load_industry_l1

    membership = r112._historical_membership()
    service = r112._service()
    candidate_curves, candidate_trades, runs = {}, {}, {}
    control_curves, control_trades = {}, {}
    for cost in COSTS:
        curve, trades, info = _run(service, membership, r112, cost)
        candidate_curves[cost], candidate_trades[cost], runs[f"{cost}x"] = curve, trades, info
        control_curves[cost] = pl.read_parquet(control_paths[str(cost)]["curve"]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        control_trades[cost] = json.loads(control_paths[str(cost)]["trades"].read_text(encoding="utf-8"))

    full_curve = candidate_curves[3]
    validation_curve = _period_curve(full_curve, VALIDATION_START, END)
    discovery_curve = _period_curve(full_curve, START, DISCOVERY_END)
    control_full = control_curves[3]
    control_validation = _period_curve(control_full, VALIDATION_START, END)
    candidate_validation_trades = _period_trades(candidate_trades[3], start=VALIDATION_START, end=END)
    control_validation_trades = _period_trades(control_trades[3], start=VALIDATION_START, end=END)
    industry_frame = load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row.get("industry_l1")}
    full_concentration = r239._aggregate(candidate_trades[3], industry)
    validation_concentration = r239._aggregate(candidate_validation_trades, industry)
    validation_positive = sum(row["pnl_amount"] > 0 for row in validation_concentration["per_symbol"])

    candidate_full_stats = _stats(full_curve, r112)
    candidate_validation_stats = _stats(validation_curve, r112)
    candidate_discovery_stats = _stats(discovery_curve, r112)
    control_full_stats = r266["full"]
    control_validation_stats = r112._stats(control_validation)
    rolling = _rolling(full_curve)
    candidate_loss_full = _loss_summary(candidate_trades[3])
    candidate_loss_validation = _loss_summary(candidate_validation_trades)
    control_loss_full = _loss_summary(control_trades[3])
    control_loss_validation = _loss_summary(control_validation_trades)
    c = full_concentration
    cv = validation_concentration
    decisions = {
        "P1": True,
        "P2": panel.get("all_checks_pass") is True,
        "G1": candidate_full_stats["total_return"] > 0 and candidate_full_stats["total_return"] >= control_full_stats["total_return"] * 0.70,
        "G2": candidate_full_stats["mdd"] >= -0.25,
        "G3": rolling["positive_rate"] >= 0.70,
        "G4": candidate_validation_stats["total_return"] > 0,
        "G5": candidate_validation_stats["annual"] >= 0.05,
        "G6": r112._stats(candidate_curves[4])["total_return"] > 0,
        "G7": r112._stats(candidate_curves[5])["total_return"] > 0,
        "G8": candidate_validation_stats["total_return"] >= control_validation_stats["total_return"] * 0.70,
        "R1": candidate_full_stats["sharpe"] >= control_full_stats["sharpe"],
        "R2": candidate_full_stats["mdd"] >= control_full_stats["mdd"] - 0.03,
        "L1": candidate_loss_full["mean_loss_magnitude_pct"] is not None and control_loss_full["mean_loss_magnitude_pct"] is not None and candidate_loss_full["mean_loss_magnitude_pct"] <= control_loss_full["mean_loss_magnitude_pct"] - 0.01,
        "L2": candidate_loss_validation["mean_loss_magnitude_pct"] is not None and control_loss_validation["mean_loss_magnitude_pct"] is not None and candidate_loss_validation["mean_loss_magnitude_pct"] <= control_loss_validation["mean_loss_magnitude_pct"] - 0.01,
        "L3": candidate_loss_full["worst_trade_return_pct"] is not None and control_loss_full["worst_trade_return_pct"] is not None and candidate_loss_full["worst_trade_return_pct"] > control_loss_full["worst_trade_return_pct"],
        "L4": candidate_loss_validation["worst_trade_return_pct"] is not None and control_loss_validation["worst_trade_return_pct"] is not None and candidate_loss_validation["worst_trade_return_pct"] > control_loss_validation["worst_trade_return_pct"],
        "C1": c["distinct_symbols"] >= 30,
        "C2": c["total_pnl_amount"] > 0 and c["top1_net_share"] <= 0.25,
        "C3": c["total_pnl_amount"] > 0 and c["top3_net_share"] <= 0.55,
        "C4": c["total_pnl_amount"] > 0 and c["top5_net_share"] <= 0.75,
        "C5": validation_positive >= 8,
        "C6": c["top_industry_gross_share"] is not None and c["top_industry_gross_share"] <= 0.45,
        "C7": c["max_symbol_loss_to_gross"] is not None and c["max_symbol_loss_to_gross"] <= 0.20,
        "C8": cv["total_pnl_amount"] > 0 and cv["top1_net_share"] <= 0.35,
    }
    gate_diagnostics = _daily_gate_diagnostics()
    exit_signal_counts = {sid: sum(row.get("exit_signal_id") == sid for row in candidate_trades[3]) for sid in SIGNAL_IDS}
    exited_by_signal = sum(exit_signal_counts.values())
    payload = {
        "round": "R273",
        "status": "complete",
        "classification": "single_variant_exploratory_test_not_independent_confirmation",
        "preregistration": PREREG.relative_to(ROOT).as_posix(),
        "preregistration_sha256": prereg_digest,
        "panel_registration": PANEL.relative_to(ROOT).as_posix(),
        "panel_registration_sha256": _sha256(PANEL),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "window": {"start": str(START), "end": str(END), "discovery_end": str(DISCOVERY_END), "validation_start": str(VALIDATION_START)},
        "execution": {"service": "StrategyBacktestService -> BacktestEngine", "matching": "open_t+1", "membership": "r78_daily_membership", "max_positions": 10, "position_sizing": "equal", "max_hold_days": 35, "costs": ["3x", "4x", "5x"], "only_changed_behavior": "OR of three existing macro gate closures, T+1 open exit", "entry_ranking_and_sizing_unchanged": True},
        "macro_gate_diagnostics": gate_diagnostics,
        "exit_diagnostics": {"signal_trigger_exit_trades": exited_by_signal, "by_signal": exit_signal_counts, "blocked_exit_days": sum(int(row.get("blocked_exit_days") or 0) for row in candidate_trades[3]), "exit_reason_counts": {reason: sum(row.get("exit_reason") == reason for row in candidate_trades[3]) for reason in sorted({str(row.get("exit_reason")) for row in candidate_trades[3]})}},
        "runs": runs,
        "candidate": {"full": candidate_full_stats, "discovery": candidate_discovery_stats, "validation": candidate_validation_stats, "rolling_1y": rolling, "trade_loss_full": candidate_loss_full, "trade_loss_validation_entry": candidate_loss_validation, "concentration_full": {key: value for key, value in c.items() if key != "per_symbol"}, "concentration_validation_entry": {key: value for key, value in cv.items() if key != "per_symbol"}, "validation_positive_symbols": validation_positive},
        "control": {"full": control_full_stats, "validation": control_validation_stats, "trade_loss_full": control_loss_full, "trade_loss_validation_entry": control_loss_validation, "concentration_full": {key: value for key, value in r239._aggregate(control_trades[3], industry).items() if key != "per_symbol"}},
        "comparison": {"full_return_retention": candidate_full_stats["total_return"] / control_full_stats["total_return"] if control_full_stats["total_return"] else None, "validation_return_retention": candidate_validation_stats["total_return"] / control_validation_stats["total_return"] if control_validation_stats["total_return"] else None, "prior_close_rebased_validation": True},
        "decision": decisions,
        "all_pass": all(decisions.values()),
        "research_decision": "eligible_for_separate_paper_review_only" if all(decisions.values()) else "research_only_rejected",
        "production_status": "pause_production",
        "limitations": prereg.get("known_limitations", []),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    OUT_HASH.write_text(f"{_sha256(OUT)}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"result": OUT.relative_to(ROOT).as_posix(), "all_pass": payload["all_pass"], "decision": decisions, "candidate_full": candidate_full_stats, "candidate_validation": candidate_validation_stats, "control_full": control_full_stats, "control_validation": control_validation_stats, "exit_diagnostics": payload["exit_diagnostics"], "sha256": _sha256(OUT)}, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0 if payload["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
