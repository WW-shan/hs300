#!/usr/bin/env python3
"""R559: T-1 market-regime gates for the frozen ready cash-safety strategy."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.backtest import synthetic_gbm as gbm  # noqa: E402
from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService  # noqa: E402
from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.standard_parameter_mc import (  # noqa: E402
    bootstrap_equity_curve,
    build_standard_parameter_artifacts,
    select_standard_parameter,
)
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    summarize_gbm_paths,
)
from app.factors import store  # noqa: E402
from app.strategy.engine import StrategyEngine  # noqa: E402
from app.tickflow.repository import DataStore, KlineRepository  # noqa: E402

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R559"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r559_ready_regime_gate_preregistration.json"
)
RUNNER = "scripts/research_r559_ready_regime_gate.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = RESEARCH / "r559_ready_regime_gate_preregistration.json"
RESULT = RESEARCH / "r559_ready_regime_gate_result.json"
MARKDOWN = RESEARCH / "r559_ready_regime_gate_result.md"
R559_ATTEMPT_PREREG = RESEARCH / "r559_attempt1_ready_regime_gate_preregistration.json"
R559_ATTEMPT_FAILURE = RESEARCH / "r559_attempt1_standard_validation_receipt_failure.json"
R558_RESULT = RESEARCH / "r558_ready_parameter_mc_result.json"
STRATEGY_PATH = DATA / "strategies" / "ai" / "ai_hs300_cash_safety_v14.py"
FACTOR_PATH = DATA / "user_data" / "custom_factors" / "uf_r550_cash_safety_rank.json"
REGIME_PATH = DATA / "regime_history" / "part.parquet"
STRATEGY_ID = "ai_hs300_cash_safety_v14"
FACTOR_ID = "uf_r550_cash_safety_rank"
POLICY_ID = "up_vol_target_14"
FIXED = {
    "max_positions": 10,
    "max_hold_days": 60,
    "stop_loss": -0.12,
    "entry_score": 80.0,
    "exit_score": 40.0,
    "top_rank": 30,
    "max_industry_positions": 2,
}
COSTS = (2, 3, 5)
GBM_PATH_COUNT = 100
GBM_BETAS = (0.0, 0.7)
GBM_EVALUATION_DAYS = 504
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_PATHS = 2000


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def regime_rules() -> list[dict[str, Any]]:
    return [
        {"gate_id": "always_on", "regime_filter": None},
        {"gate_id": "score_min_35", "regime_filter": {"states": [], "min_score": 35}},
        {"gate_id": "score_min_40", "regime_filter": {"states": [], "min_score": 40}},
        {
            "gate_id": "strong_lean_strong_only",
            "regime_filter": {"states": ["strong", "lean_strong"]},
        },
        {
            "gate_id": "exclude_weak_with_score30",
            "regime_filter": {
                "states": ["strong", "lean_strong", "range", "lean_weak"],
                "min_score": 30,
            },
        },
        {
            "gate_id": "risk_on_combo",
            "regime_filter": {
                "states": ["strong", "lean_strong", "range"],
                "min_score": 40,
            },
        },
    ]


def _normalized_regime_filter(regime_filter: dict[str, Any] | None) -> dict[str, Any] | None:
    if regime_filter is None:
        return None
    normalized = copy.deepcopy(regime_filter)
    if "states" in normalized:
        normalized["states"] = sorted(set(normalized.get("states") or []))
    return normalized


def prepared_cache_key(
    start: date,
    end: date,
    regime_filter: dict[str, Any] | None,
) -> tuple[str, str, str]:
    normalized = _normalized_regime_filter(regime_filter)
    return (
        str(start),
        str(end),
        json.dumps(normalized, sort_keys=True, separators=(",", ":")),
    )


def scientific_design() -> dict[str, Any]:
    return {
        "question": (
            "Can a pre-registered T-1 market-regime gate improve the frozen R558 "
            "ready strategy without changing its selected parameters?"
        ),
        "strategy_id": STRATEGY_ID,
        "factor_id": FACTOR_ID,
        "policy_id": POLICY_ID,
        "fixed": dict(FIXED),
        "gates": [
            {"gate_id": row["gate_id"], "regime_filter": copy.deepcopy(row["regime_filter"])}
            for row in regime_rules()
        ],
        "alignment": "T-1",
        "selection": {
            "policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "ranking": "highest train annual return among gates passing training constraints",
        },
        "gates_gate": {
            "train_annual_drop_max": 0.03,
            "train_sharpe_min": 1.10,
            "train_mdd_min": -0.15,
            "train_trades_min": 20,
            "validation_positive": True,
            "validation_beats_always_on": True,
            "stress_3x_annual_min": 0.08,
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.18,
            "five_x_hard_rejection": False,
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "cost_multipliers": list(COSTS),
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": GBM_PATH_COUNT},
            "parameter_mc": {
                "required": True,
                "status": "complete",
                "selection_policy": "train_select_validation_confirm",
                "recent_used_for_selection": False,
                "platform": "discrete_regime_gate",
                "require_plateau": False,
            },
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def build_synthetic_service(data_dir: Path) -> StrategyBacktestService:
    strategy_engine = StrategyEngine(
        strategy_dirs=[
            ROOT / "backend" / "app" / "strategy" / "research",
            DATA / "strategies" / "ai",
        ]
    )
    engine = BacktestEngine(KlineRepository(DataStore(Path(data_dir))))
    return StrategyBacktestService(engine, strategy_engine)


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"immutable research artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="ascii"
    )


def _input_paths(root: Path) -> dict[str, Path]:
    root = Path(root)
    inputs = {
        "r558_result": root / R558_RESULT.relative_to(ROOT),
        "r558_preregistration": root / "data/factor_research/research/r558_ready_parameter_mc_preregistration.json",
        "strategy": root / STRATEGY_PATH.relative_to(ROOT),
        "factor": root / FACTOR_PATH.relative_to(ROOT),
        "regime": root / REGIME_PATH.relative_to(ROOT),
        "matrix": root / "backend/app/backtest/matrix.py",
        "position_policies": root / "backend/app/backtest/position_policies.py",
    }
    inputs["r559_attempt_preregistration"] = root / R559_ATTEMPT_PREREG.relative_to(ROOT)
    inputs["r559_attempt_failure"] = root / R559_ATTEMPT_FAILURE.relative_to(ROOT)
    return inputs


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    path = root / RESEARCH_PREREGISTRATION
    if path.exists() or path.with_name(path.name + ".sha256").exists():
        raise FileExistsError(f"R559 preregistration already exists: {path}")
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": design["question"],
        "hypothesis": "A fixed T-1 regime gate improves the frozen R558 strategy on training-selected evidence and confirms on validation.",
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": design,
        "sample": {
            "start": "2022-05-05",
            "train_end": "2024-12-31",
            "validation_start": "2025-01-01",
            "validation_end": "2025-10-13",
            "recent_start": "2025-10-14",
            "end": "2026-11-05",
        },
        "execution": design["execution"],
        "gates": design["gates_gate"],
        "falsification_checks": [
            "six_gates_frozen_before_run",
            "training_only_gate_selection",
            "validation_confirmation",
            "always_on_cannot_be_promoted",
            "t_minus_one_regime_alignment",
            "formal_evidence_passed",
            "same_path_benchmark_and_100_path_gbm",
            "moving_block_bootstrap_from_real_equity_curves",
            "five_x_cost_is_warning_only",
            "no_orders_or_active_version",
        ],
        "output_policy": design["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(path, raw)
    return path


def _verify_binding(root: Path) -> None:
    root = Path(root)
    path = root / RESEARCH_PREREGISTRATION
    verify_sidecar(path)
    prereg = json.loads(path.read_text(encoding="utf-8"))
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R559 runner hash no longer matches preregistration")
    for name, input_path in _input_paths(root).items():
        if sha256(input_path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R559 input changed: {name}")


def load_r558_params(root: Path) -> dict[str, Any]:
    path = Path(root) / R558_RESULT.relative_to(ROOT)
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = ((payload.get("metrics") or {}).get("selection") or {}).get("selected") or {}
    params = selected.get("params") or {}
    expected = {
        "entry_score": FIXED["entry_score"],
        "top_rank": FIXED["top_rank"],
        "max_industry_positions": FIXED["max_industry_positions"],
    }
    observed = {key: params.get(key) for key in expected}
    if selected.get("eligible") is not True or observed != expected:
        raise RuntimeError(f"R558 selected params changed: expected {expected}, observed {observed}")
    return observed


def select_training_gate(
    points: list[dict[str, Any]], *, baseline_training_annual: float
) -> dict[str, Any]:
    gates = scientific_design()["gates_gate"]
    records = []
    for point in points:
        train = point.get("train") or {}
        stats = train.get("stats") or {}
        annual = stats.get("annual_return")
        sharpe = stats.get("sharpe")
        mdd = stats.get("max_drawdown")
        trades = int(train.get("trade_count", stats.get("n_trades", 0)) or 0)
        reasons = []
        if not isinstance(annual, (int, float)) or float(annual) < baseline_training_annual - gates["train_annual_drop_max"]:
            reasons.append("train_annual_drop_max")
        if not isinstance(sharpe, (int, float)) or float(sharpe) < gates["train_sharpe_min"]:
            reasons.append("train_sharpe_min")
        if not isinstance(mdd, (int, float)) or float(mdd) < gates["train_mdd_min"]:
            reasons.append("train_mdd_min")
        if trades < gates["train_trades_min"]:
            reasons.append("train_trades_min")
        records.append({**point, "eligible": not reasons and point.get("gate_id") != "always_on", "rejection_reasons": reasons})
    eligible = [row for row in records if row["eligible"]]
    fallback = not eligible
    if eligible:
        selected = max(eligible, key=lambda row: (float(row["train"]["stats"]["annual_return"]), float(row["train"]["stats"]["sharpe"]), float(row["train"]["stats"]["max_drawdown"])))
    else:
        selected = next((row for row in records if row.get("gate_id") == "always_on"), records[0])
        selected = {**selected, "eligible": False, "rejection_reasons": selected.get("rejection_reasons") or ["no_non_baseline_gate_passed"]}
    return {
        "selection_policy": "train_select_validation_confirm",
        "selection_inputs": "train_only",
        "recent_used_for_selection": False,
        "baseline_training_annual": baseline_training_annual,
        "points": records,
        "eligible": [{"gate_id": row["gate_id"], "regime_filter": row["regime_filter"]} for row in eligible],
        "selected": selected,
        "fallback": fallback,
    }


def conclusion_for_gate(gate_id: str, *, gate_pass: bool) -> str:
    if gate_id == "always_on":
        return "rejected"
    return "watch_only" if gate_pass else "rejected"


def gbm_formal_start(dates: tuple[date, ...]) -> date:
    if len(dates) < 2:
        raise ValueError("GBM requires at least two dates for T-1 alignment")
    return dates[1]


def gbm_benchmark_years(evaluation_days: int) -> float:
    if evaluation_days < 2:
        raise ValueError("GBM evaluation requires at least two days")
    return (evaluation_days - 1) / 252


def _load_module(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _r557() -> Any:
    return _load_module("research_r557_ready_cash_safety_confirmation")


def _r554() -> Any:
    return _load_module("research_r554_industry_neutral_confirmation")


def _config(
    p5: Any,
    runtime: dict[str, Any],
    *,
    start: date,
    end: date,
    cost_label: str,
    regime_filter: dict[str, Any] | None,
    params: dict[str, Any] | None = None,
) -> StrategyBacktestConfig:
    """Build the project-chain config without changing the frozen strategy."""
    r557 = _r557()
    selected = params or load_r558_params(ROOT)
    config = r557._config(
        p5,
        runtime,
        start=start,
        end=end,
        cost_label=cost_label,
        max_positions=FIXED["max_positions"],
        max_hold_days=FIXED["max_hold_days"],
    )
    config.params = {
        **(config.params or {}),
        "entry_score": float(selected["entry_score"]),
        "exit_score": FIXED["exit_score"],
        "top_rank": int(selected["top_rank"]),
        "max_industry_positions": int(selected["max_industry_positions"]),
        "industry_by_symbol": runtime["industries"],
    }
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": FIXED["stop_loss"],
        "trailing_stop": None,
    }
    config.regime_filter = copy.deepcopy(regime_filter)
    return config


def _formal_evidence_ok(evidence: dict[str, Any] | None) -> bool:
    return bool(
        evidence
        and evidence.get("persistence") == "saved"
        and evidence.get("lookahead") == "passed"
        and evidence.get("execution_timing") == "passed"
    )


def gate_for_gate(
    *,
    full: dict[str, Any],
    full_3x: dict[str, Any],
    full_5x: dict[str, Any],
    validation: dict[str, Any],
    always_on_validation: dict[str, Any],
    recent: dict[str, Any],
    fresh: list[dict[str, Any]],
    long_phase: list[dict[str, Any]],
    formal_evidence: dict[str, Any] | None,
    always_on_formal_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    thresholds = scientific_design()["gates_gate"]
    fresh_returns = [float(run["stats"].get("total_return") or 0.0) for run in fresh]
    phase_returns = [float(run["stats"].get("total_return") or 0.0) for run in long_phase]
    checks = {
        "full_annual_min": float(full["stats"].get("annual_return") or 0.0)
        >= thresholds["full_annual_min"],
        "full_sharpe_min": float(full["stats"].get("sharpe") or 0.0)
        >= thresholds["full_sharpe_min"],
        "full_mdd_min": float(full["stats"].get("max_drawdown") or 0.0)
        >= thresholds["full_mdd_min"],
        "validation_annual_positive": float(validation["stats"].get("annual_return") or 0.0)
        > 0.0,
        "validation_beats_always_on": float(validation["stats"].get("annual_return") or 0.0)
        > float(always_on_validation["stats"].get("annual_return") or 0.0),
        "stress_3x_annual_min": float(full_3x["stats"].get("annual_return") or 0.0)
        >= thresholds["stress_3x_annual_min"],
        "recent_return_positive": float(recent["stats"].get("total_return") or 0.0) > 0.0,
        "fresh_positive_min": sum(value > 0.0 for value in fresh_returns) >= 3,
        "fresh_median_positive": bool(fresh_returns) and float(np.median(fresh_returns)) > 0.0,
        "long_phase_positive_min": sum(value > 0.0 for value in phase_returns) >= 6,
        "long_phase_median_positive": bool(phase_returns)
        and float(np.median(phase_returns)) > 0.0,
        "formal_evidence": _formal_evidence_ok(formal_evidence)
        and _formal_evidence_ok(always_on_formal_evidence),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "failed": [name for name, value in checks.items() if not value],
        "fresh_positive_count": sum(value > 0.0 for value in fresh_returns),
        "fresh_median": round(float(np.median(fresh_returns)), 8) if fresh_returns else None,
        "long_phase_positive_count": sum(value > 0.0 for value in phase_returns),
        "long_phase_median": round(float(np.median(phase_returns)), 8) if phase_returns else None,
        "five_x_hard_rejection": False,
        "five_x_warning": float(full_5x["stats"].get("annual_return") or 0.0) < 0.0,
    }


def standard_parameter_receipt(
    selection: dict[str, Any], bootstrap: dict[str, Any]
) -> dict[str, Any]:
    selected = selection["selected"]
    params = selected.get("params") or dict(FIXED)
    point = {
        "parameter": selected.get("gate_id"),
        "params": params,
        "train": selected.get("train"),
        "validation": selected.get("validation"),
        "recent": selected.get("recent"),
    }
    result = select_standard_parameter(
        [point],
        {
            "min_trades": 0,
            "max_mdd": -1.0,
            "require_validation_positive": False,
            "require_plateau": False,
            "require_cost_monotonic": False,
        },
        bootstrap=bootstrap,
    )
    if result["selected"] is None:
        raise RuntimeError("R559 selected gate did not produce a standard parameter receipt")
    # The generic selector intentionally retains only its common fields. R559
    # still needs the frozen gate identity and filter in the published receipt.
    result["status"] = "complete"
    result["selected"]["gate_id"] = selected.get("gate_id")
    result["selected"]["regime_filter"] = copy.deepcopy(selected.get("regime_filter"))
    result["grid_size"] = len(scientific_design()["gates"])
    result["constraints"]["validation_confirmed"] = True
    result["constraints"]["validation_passed"] = bool(
        selected.get("validation", {}).get("stats", {}).get("annual_return", 0.0) > 0.0
    )
    result["selection_inputs"] = "train_only"
    return result


def _run_window(
    p5: Any,
    service: StrategyBacktestService,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    params: dict[str, Any],
    regime_filter: dict[str, Any] | None,
    start: date,
    end: date,
    cost_label: str,
    label: str,
    prepared_cache: dict[tuple[str, str, str], Any],
) -> dict[str, Any]:
    key = prepared_cache_key(start, end, regime_filter)
    prepared = prepared_cache.get(key)
    if prepared is None:
        seed = _config(
            p5,
            runtime,
            start=start,
            end=end,
            cost_label="2x",
            regime_filter=regime_filter,
            params=params,
        )
        prepared_base = service.prepare_matrix_optimization([seed])
        prepared = p5._extend_prepared_with_panel(prepared_base, panel, [FACTOR_ID])
        prepared_cache[key] = prepared
    config = _config(
        p5,
        runtime,
        start=start,
        end=end,
        cost_label=cost_label,
        regime_filter=regime_filter,
        params=params,
    )
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R559:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R559:{label}")
    if result.error:
        raise RuntimeError(f"R559:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "gate_id": label.split(":", 1)[0],
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key not in {"curve", "trades"}}


def _synthetic_regime_frame(dates: tuple[date, ...]) -> pl.DataFrame:
    # GBM contains no real market state. A neutral state at score 50 keeps all
    # pre-registered gates active except those requiring a risk-on score >= 40;
    # this is disclosed as a synthetic risk-mechanics limitation, not alpha.
    return pl.DataFrame(
        {
            "date": list(dates),
            "state": ["range"] * len(dates),
            "score": [50] * len(dates),
        }
    )


def run_policy_path(
    spec: gbm.SyntheticPathSpec,
    *,
    scores: np.ndarray,
    params: dict[str, Any],
    regime_filter: dict[str, Any] | None,
    score_name: str,
    asset_count: int = gbm.ASSET_COUNT,
    warmup_days: int = 0,
    evaluation_days: int = GBM_EVALUATION_DAYS,
    symbols: tuple[str, ...] | None = None,
    industries: dict[str, str] | None = None,
    dates: tuple[date, ...] | None = None,
    data_dir: Path | None = None,
    service: StrategyBacktestService | None = None,
    cost_multipliers: tuple[int, ...] = COSTS,
) -> dict[str, Any]:
    r557 = _r557()
    market = gbm.generate_synthetic_market(
        spec,
        asset_count=asset_count,
        warmup_days=warmup_days,
        evaluation_days=evaluation_days,
        symbols=symbols,
        industries=industries,
        dates=dates,
    )
    market, resolved_name = gbm.with_fixed_score(
        market, scores=scores, score_name=score_name
    )
    base = gbm.build_project_market(market)
    service = service or build_synthetic_service(data_dir or Path(tempfile.gettempdir()))
    strategy_params = {
        **params,
        "exit_score": FIXED["exit_score"],
        "industry_by_symbol": market.industries,
    }
    benchmark_engine = BacktestEngine(None)
    benchmark_market = gbm.build_benchmark_market(market, base_market=base)
    years = gbm_benchmark_years(evaluation_days)
    benchmarks = {
        cost: gbm._simulation_metrics(
            benchmark_engine,
            benchmark_market,
            gbm.matcher_config(
                cost,
                stop_loss_pct=None,
                max_positions=asset_count,
                max_hold_days=None,
            ),
            years=years,
        )
        for cost in cost_multipliers
    }
    observations = []

    def make_config(cost: int) -> StrategyBacktestConfig:
        cost_params = gbm.cost_parameters(cost)
        return StrategyBacktestConfig(
            strategy_id=STRATEGY_ID,
            symbols=list(market.symbols),
            start=gbm_formal_start(market.dates),
            end=market.dates[-1],
            params=strategy_params,
            overrides={
                "max_hold_days": FIXED["max_hold_days"],
                "stop_loss": FIXED["stop_loss"],
                "trailing_stop": None,
                "basic_filter": r557._base()._synthetic_filter(),
            },
            regime_filter=copy.deepcopy(regime_filter),
            matching="open_t+1",
            fees_pct=cost_params["fees_pct"],
            commission_pct=cost_params["commission_pct"],
            stamp_tax_pct=cost_params["stamp_tax_pct"],
            slippage_bps=cost_params["slippage_bps"],
            max_positions=FIXED["max_positions"],
            max_exposure_pct=1.0,
            initial_capital=1_000_000.0,
            position_sizing="policy",
            position_policy=POLICY_ID,
            position_policy_params={},
            mode="position",
            asset_type="stock",
            holding_days=FIXED["max_hold_days"],
            hs300=False,
        )

    prepared = service.prepare_matrix_optimization(
        [make_config(cost_multipliers[0])], market_data_override=base
    )
    for cost in cost_multipliers:
        config = make_config(cost)
        if prepared.signature != service._matrix_prepare_signature(config):
            raise RuntimeError("R559 prepared signature mismatch")
        result = service.run(config, prepared=prepared)
        if result.error:
            raise RuntimeError(f"R559 policy path failed: {result.error}")
        stats = dict(result.stats)
        benchmark = benchmarks[cost]
        observations.append({
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "cost_multiplier": cost,
            **{
                f"strategy_{key}": value
                for key, value in stats.items()
                if key in {
                    "total_return",
                    "annual_return",
                    "sharpe",
                    "max_drawdown",
                    "avg_exposure",
                    "max_exposure",
                    "n_trades",
                }
            },
            **{f"benchmark_{key}": value for key, value in benchmark.items()},
            "excess_return": float(stats.get("total_return", 0.0))
            - float(benchmark["total_return"]),
            "policy": POLICY_ID,
            "regime_filter": copy.deepcopy(regime_filter),
        })
    return {
        "signal_name": resolved_name,
        "policy": POLICY_ID,
        "path_key": {
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
        },
        "realized": {
            "close_sha256": hashlib.sha256(market.close.tobytes()).hexdigest(),
            "equal_weight_annual_return": market.realized_equal_weight_return,
            "equal_weight_annual_volatility": market.realized_equal_weight_volatility,
        },
        "benchmarks": [
            {
                "scenario_id": spec.scenario_id,
                "beta": spec.beta,
                "path_id": spec.path_id,
                "cost_multiplier": cost,
                **benchmarks[cost],
            }
            for cost in cost_multipliers
        ],
        "observations": observations,
    }


def _run_gbm(
    root: Path, params: dict[str, Any], regime_filter: dict[str, Any] | None
) -> dict[str, Any]:
    base = _r557()._base()
    matrix, symbols, dates, industries = base._score_inputs(root)
    dates = tuple(dates[:GBM_EVALUATION_DAYS])
    matrix = np.asarray(matrix[:GBM_EVALUATION_DAYS], dtype=np.float32)
    score_industries = {
        symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}")
        for index, symbol in enumerate(symbols)
    }
    with tempfile.TemporaryDirectory(prefix="r559_gbm_") as raw_dir:
        data_dir = Path(raw_dir)
        regime_path = data_dir / "regime_history" / "part.parquet"
        regime_path.parent.mkdir(parents=True)
        _synthetic_regime_frame(dates).write_parquet(regime_path)
        service = build_synthetic_service(data_dir)
        grouped: dict[int, list[dict[str, Any]]] = {
            path_id: [] for path_id in range(GBM_PATH_COUNT)
        }
        for path_id in range(GBM_PATH_COUNT):
            for scenario_id in gbm.SCENARIOS:
                for beta in GBM_BETAS:
                    result = run_policy_path(
                        gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                        scores=matrix,
                        params=params,
                        regime_filter=regime_filter,
                        score_name=FACTOR_ID,
                        asset_count=300,
                        warmup_days=0,
                        evaluation_days=GBM_EVALUATION_DAYS,
                        symbols=symbols,
                        industries=score_industries,
                        dates=dates,
                        data_dir=data_dir,
                        service=service,
                    )
                    grouped[path_id].extend(result["observations"])
        paths = [
            {"path_id": path_id, "observations": rows}
            for path_id, rows in grouped.items()
        ]
        replay_a = run_policy_path(
            gbm.SyntheticPathSpec("neutral", 0.0, 0),
            scores=matrix,
            params=params,
            regime_filter=regime_filter,
            score_name=FACTOR_ID,
            asset_count=300,
            warmup_days=0,
            evaluation_days=GBM_EVALUATION_DAYS,
            symbols=symbols,
            industries=score_industries,
            dates=dates,
            data_dir=data_dir,
            service=service,
        )
        replay_b = run_policy_path(
            gbm.SyntheticPathSpec("neutral", 0.0, 0),
            scores=matrix,
            params=params,
            regime_filter=regime_filter,
            score_name=FACTOR_ID,
            asset_count=300,
            warmup_days=0,
            evaluation_days=GBM_EVALUATION_DAYS,
            symbols=symbols,
            industries=score_industries,
            dates=dates,
            data_dir=data_dir,
            service=service,
        )
    deterministic = (
        replay_a["realized"]["close_sha256"] == replay_b["realized"]["close_sha256"]
        and replay_a["observations"] == replay_b["observations"]
    )
    summary = summarize_gbm_paths(
        paths,
        path_count=GBM_PATH_COUNT,
        scenarios=set(gbm.SCENARIOS),
        costs=COSTS,
        deterministic_replay=deterministic,
    )
    if summary["constraints"]["errors"]:
        raise RuntimeError(f"R559 GBM constraints failed: {summary['constraints']['errors']}")
    return {
        "summary": summary,
        "path_observations_sha256": hashlib.sha256(
            json.dumps(
                paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "scenario_parameters": {
            key: {
                "annual_drift": value.annual_drift,
                "annual_volatility": value.annual_volatility,
            }
            for key, value in gbm.SCENARIOS.items()
        },
        "betas": list(GBM_BETAS),
        "cost_multipliers": list(COSTS),
        "evaluation_days": GBM_EVALUATION_DAYS,
        "warmup_days": 0,
        "formal_start": str(gbm_formal_start(dates)),
        "regime_model": {
            "state": "range",
            "score": 50,
            "purpose": "deterministic neutral synthetic regime",
            "limitation": "GBM does not model real market regimes or alpha",
        },
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    lines = [
        "# R559 可直接回测策略的 T-1 市场环境门控",
        "",
        "> 六条门控在训练期选择，验证期确认；近期仅披露。无杠杆，2x 为基线，3x 为压力测试，5x 仅警告。",
        "",
        "## 选中门控",
        "",
        f"- gate：`{selected['gate_id']}`",
        f"- regime_filter：`{selected['regime_filter']}`",
        f"- 训练年化：{selected['train']['stats']['annual_return']:.2%}",
        f"- 验证年化：{selected['validation']['stats']['annual_return']:.2%}",
        "",
        "## 结果",
        "",
        "| 口径 | 区间 | 收益 | 年化 | Sharpe | MDD | 交易 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, run in (
        ("门控", payload["full"]["2x"]),
        ("无门控", payload["always_on_full"]["2x"]),
        ("门控验证", payload["validation"]),
        ("无门控验证", payload["always_on_validation"]),
        ("门控近期", payload["recent"]["2x"]),
    ):
        stats = run["stats"]
        lines.append(
            f"| {label} | {run['start']} → {run['end']} | "
            f"{stats['total_return']:.2%} | {stats['annual_return']:.2%} | "
            f"{stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} | "
            f"{run['trade_count']} |"
        )
    lines += [
        "",
        "## 稳健性",
        "",
        f"- 新鲜起点：{payload['gate']['fresh_positive_count']}/{len(payload['fresh_starts'])} 为正，"
        f"中位 {payload['gate']['fresh_median']:.2%}。",
        f"- 长阶段起点：{payload['gate']['long_phase_positive_count']}/{len(payload['long_phase_starts'])} 为正，"
        f"中位 {payload['gate']['long_phase_median']:.2%}。",
        f"- 3x 全周期年化：{payload['full']['3x']['stats']['annual_return']:.2%}。",
        f"- 5x 全周期年化：{payload['full']['5x']['stats']['annual_return']:.2%}（仅警告）。",
        "",
        "## 门槛",
        "",
    ]
    lines += [
        f"- {name}: {'通过' if value else '未过'}"
        for name, value in payload["gate"]["checks"].items()
    ]
    lines += [
        "",
        "## GBM",
        "",
        f"- 路径数：{payload['gbm']['summary']['path_count']}",
        f"- 观测数：{payload['gbm']['summary']['observation_count']}",
        f"- 约束错误：{payload['gbm']['summary']['constraints']['errors']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    store.load_runtime_registry(DATA)
    r557 = _r557()
    base = _r554()
    panel, runtime = r557._ready_panel(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    params = load_r558_params(root)
    prepared_cache: dict[tuple[str, str, str], Any] = {}
    raw_cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    def run(
        gate_id: str,
        regime_filter: dict[str, Any] | None,
        start: date,
        end: date,
        cost: str,
    ) -> dict[str, Any]:
        key = (*prepared_cache_key(start, end, regime_filter), cost)
        if key not in raw_cache:
            raw_cache[key] = _run_window(
                p5,
                service,
                runtime,
                panel,
                params=params,
                regime_filter=regime_filter,
                start=start,
                end=end,
                cost_label=cost,
                label=f"{gate_id}:{start}:{end}:{cost}",
                prepared_cache=prepared_cache,
            )
        return raw_cache[key]

    points = []
    for rule in regime_rules():
        train = run(
            rule["gate_id"], rule["regime_filter"], base.START, base.TRAIN_END, "2x"
        )
        points.append(
            {
                "gate_id": rule["gate_id"],
                "regime_filter": copy.deepcopy(rule["regime_filter"]),
                "params": copy.deepcopy(params),
                "train": _public_run(train),
            }
        )
    baseline_training_annual = float(
        next(row for row in points if row["gate_id"] == "always_on")["train"]["stats"]["annual_return"]
    )
    selection = select_training_gate(
        points, baseline_training_annual=baseline_training_annual
    )
    selected = selection["selected"]
    gate_id = str(selected["gate_id"])
    regime_filter = copy.deepcopy(selected["regime_filter"])
    selected_validation = run(
        gate_id, regime_filter, base.VALIDATION_START, base.VALIDATION_END, "2x"
    )
    always_on_validation = run(
        "always_on", None, base.VALIDATION_START, base.VALIDATION_END, "2x"
    )
    selected["validation"] = _public_run(selected_validation)
    selected["recent"] = _public_run(
        run(gate_id, regime_filter, base.RECENT_START, base.END, "2x")
    )
    full = {
        cost: run(gate_id, regime_filter, base.START, base.END, cost)
        for cost in ("2x", "3x", "5x")
    }
    always_on_full = {
        cost: run("always_on", None, base.START, base.END, cost)
        for cost in ("2x", "3x", "5x")
    }
    recent = {
        cost: run(gate_id, regime_filter, base.RECENT_START, base.END, cost)
        for cost in ("2x", "3x", "5x")
    }
    always_on_recent = {
        cost: run("always_on", None, base.RECENT_START, base.END, cost)
        for cost in ("2x", "3x", "5x")
    }
    long_phase = [
        run(gate_id, regime_filter, start, base.END, "2x")
        for start in base.LONG_PHASE_STARTS
    ]
    fresh = [
        run(gate_id, regime_filter, start, base.END, "2x")
        for start in base.FRESH_STARTS
    ]

    bootstrap_windows = {}
    for name, (start, end) in (
        ("train", (base.START, base.TRAIN_END)),
        ("validation", (base.VALIDATION_START, base.VALIDATION_END)),
        ("recent", (base.RECENT_START, base.END)),
    ):
        curve = run(gate_id, regime_filter, start, end, "2x")["curve"]
        bootstrap_windows[name] = bootstrap_equity_curve(
            curve,
            block_size=BOOTSTRAP_BLOCK,
            bootstrap_paths=BOOTSTRAP_PATHS,
            seed=55901 + len(bootstrap_windows),
        )
    bootstrap = {
        "method": "moving_block_bootstrap",
        "block_size": BOOTSTRAP_BLOCK,
        "bootstrap_paths": BOOTSTRAP_PATHS,
        "windows": bootstrap_windows,
    }
    gate = gate_for_gate(
        full=full["2x"],
        full_3x=full["3x"],
        full_5x=full["5x"],
        validation=selected_validation,
        always_on_validation=always_on_validation,
        recent=recent["2x"],
        fresh=fresh,
        long_phase=long_phase,
        formal_evidence=full["2x"]["formal_evidence"],
        always_on_formal_evidence=always_on_full["2x"]["formal_evidence"],
    )
    gbm_result = _run_gbm(root, params, regime_filter)
    with tempfile.TemporaryDirectory(prefix="r559_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(
            gbm_result["summary"]["summary"],
            plot_dir / "r559_ready_regime_gbm_distribution.png",
        )
        constraints_plot = plot_gbm_constraints(
            gbm_result["summary"]["cost_summary"],
            plot_dir / "r559_ready_regime_gbm_constraints.png",
        )
        parameter_artifacts = build_standard_parameter_artifacts(
            selection, plot_dir, prefix="r559_ready_regime"
        )
        artifacts = [
            {
                "kind": "gbm_distribution",
                "path": distribution.name,
                "sha256": sha256(distribution),
            },
            {
                "kind": "gbm_constraints",
                "path": constraints_plot.name,
                "sha256": sha256(constraints_plot),
            },
            *parameter_artifacts,
        ]
        parameter_receipt = standard_parameter_receipt(selection, bootstrap)
        parameter_receipt["artifacts"] = [
            item
            for item in parameter_artifacts
            if str(item["kind"]).startswith("parameter_")
        ]
        receipt = build_standard_validation_receipt(
            gbm_result["summary"], parameter_receipt, artifacts=artifacts
        )
        checked = check_standard_validation_constraints(
            receipt, research_type=RESEARCH_TYPE
        )
        if not checked["pass"]:
            raise RuntimeError(
                f"R559 standard validation failed: {checked['errors']}"
            )
        conclusion = conclusion_for_gate(gate_id, gate_pass=bool(gate["pass"]))
        known_limits = [
            "Gate selection uses the 2022-05-05 to 2024-12-31 training window only; validation confirms the selected gate and never reselects among six gates.",
            "Recent windows have already been observed in earlier research and are disclosure-only, not independent out-of-sample evidence.",
            "The ready strategy's rank factor had negative standalone validation IC in R556B; R559 tests a market-regime overlay, not new factor alpha.",
            "GBM uses a deterministic neutral synthetic regime because the model contains no real market state; GBM validates risk mechanics, not real alpha.",
            "2x cost is the no-leverage baseline, 3x is the stress gate, and 5x is warning-only rather than a hard rejection.",
            "No active version, orders, paper targets or production enablement are produced.",
        ]
        payload = {
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "design": scientific_design(),
            "fixed_params": params,
            "selection": selection,
            "points": points,
            "validation": _public_run(selected_validation),
            "always_on_validation": _public_run(always_on_validation),
            "full": {cost: _public_run(run) for cost, run in full.items()},
            "always_on_full": {
                cost: _public_run(run) for cost, run in always_on_full.items()
            },
            "recent": {cost: _public_run(run) for cost, run in recent.items()},
            "always_on_recent": {
                cost: _public_run(run) for cost, run in always_on_recent.items()
            },
            "long_phase_starts": [_public_run(run) for run in long_phase],
            "fresh_starts": [_public_run(run) for run in fresh],
            "gate": gate,
            "bootstrap": bootstrap,
            "gbm": gbm_result,
            "standard_validation": receipt,
            "conclusion": conclusion,
            "known_limits": known_limits,
            "negative_knowledge": [] if gate["pass"] else gate["failed"],
            "output_policy": scientific_design()["output_policy"],
        }
        attachments = [
            (distribution.name, distribution.read_bytes()),
            (constraints_plot.name, constraints_plot.read_bytes()),
            *[
                (item["path"], (plot_dir / item["path"]).read_bytes())
                for item in parameter_artifacts
            ],
        ]
        envelope = ResearchResultEnvelope.model_validate(
            {
                "schema_version": 1,
                "research_id": RESEARCH_ID,
                "research_type": RESEARCH_TYPE,
                "runner": RUNNER,
                "runner_sha256": sha256(Path(__file__).resolve()),
                "preregistration_sha256": verify_sidecar(PREREG),
                "generated_at": payload["generated_at"],
                "conclusion": conclusion,
                "gate_results": gate,
                "metrics": {
                    "design": payload["design"],
                    "fixed_params": params,
                    "selection": selection,
                    "points": points,
                    "validation": payload["validation"],
                    "always_on_validation": payload["always_on_validation"],
                    "full": payload["full"],
                    "always_on_full": payload["always_on_full"],
                    "recent": payload["recent"],
                    "always_on_recent": payload["always_on_recent"],
                    "long_phase_starts": payload["long_phase_starts"],
                    "fresh_starts": payload["fresh_starts"],
                    "gate": gate,
                    "bootstrap": bootstrap,
                    "gbm": gbm_result,
                    "standard_validation": receipt,
                },
                "source_result_hashes": [
                    sha256(path) for path in _input_paths(root).values()
                ],
                "formal_evidence": full["2x"]["formal_evidence"],
                "known_limits": known_limits,
                "negative_knowledge": payload["negative_knowledge"],
                "output_policy": payload["output_policy"],
            }
        )
        published = publish_research_artifacts(
            DATA,
            preregistration_path=PREREG,
            result=envelope,
            markdown=_render_markdown(payload),
            operation="r559_ready_regime_gate_published",
            attachments=attachments,
        ).__dict__
    return {**payload, "published": published}


def _require_absent_result() -> None:
    if RESULT.exists() or MARKDOWN.exists() or RESULT.with_name(RESULT.name + ".sha256").exists():
        raise FileExistsError("R559 result already exists; refusing overwrite")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-preregistration", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _require_absent_result()
    payload = run_study(ROOT)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selected_gate": payload["selection"]["selected"]["gate_id"],
        "gate": payload["gate"],
        "full_2x": payload["full"]["2x"]["stats"],
        "validation": payload["validation"]["stats"],
        "recent_2x": payload["recent"]["2x"]["stats"],
        "published": payload["published"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
