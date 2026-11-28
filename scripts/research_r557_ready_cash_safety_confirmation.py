#!/usr/bin/env python3
"""R557: confirm the UI-selectable cash-safety strategy through the project chain."""

from __future__ import annotations

import argparse
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
from app.factor_research.extended_parameter_data import load_existing_ext_factors  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.standard_parameter_mc import bootstrap_equity_curve  # noqa: E402
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    plot_parameter_bootstrap,
    summarize_gbm_paths,
)
from app.factors import store  # noqa: E402
from app.strategy import scoring  # noqa: E402
from app.strategy.engine import StrategyEngine  # noqa: E402

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R557C"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r557c_ready_cash_safety_confirmation_preregistration.json"
)
RUNNER = "scripts/research_r557_ready_cash_safety_confirmation.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = RESEARCH / "r557c_ready_cash_safety_confirmation_preregistration.json"
RESULT = RESEARCH / "r557c_ready_cash_safety_confirmation_result.json"
MARKDOWN = RESEARCH / "r557c_ready_cash_safety_confirmation_result.md"
R557_ATTEMPT_PREREG = RESEARCH / "r557_ready_cash_safety_confirmation_preregistration.json"
R557_ATTEMPT_FAILURE = RESEARCH / "r557_attempt1_cost_adapter_failure.json"
R557B_ATTEMPT_PREREG = RESEARCH / "r557b_ready_cash_safety_confirmation_preregistration.json"
R557B_ATTEMPT_FAILURE = RESEARCH / "r557_attempt2_prereg_extra_field_failure.json"
STRATEGY_ID = "ai_hs300_cash_safety_v14"
POLICY_ID = "up_vol_target_14"
FACTOR_ID = "uf_r550_cash_safety_rank"
STRATEGY_PATH = DATA / "strategies" / "ai" / f"{STRATEGY_ID}.py"
FACTOR_PATH = DATA / "user_data" / "custom_factors" / f"{FACTOR_ID}.json"
R552_RESULT = RESEARCH / "r552c_cash_safety_parameter_mc_result.json"
R554_RESULT = RESEARCH / "r554_industry_neutral_confirmation_result.json"
R555_RESULT = RESEARCH / "r555b_neutralization_shrink_mc_result.json"
R556_RESULT = RESEARCH / "r556b_cash_safety_factor_bundle_result.json"
FIXED_PARAMETERS = {
    "max_positions": 10,
    "max_hold_days": 60,
    "stop_loss": -0.12,
    "entry_score": 70.0,
    "exit_score": 40.0,
    "top_rank": 20,
    "max_industry_positions": 3,
}
COSTS = (2, 3, 5)
GBM_PATH_COUNT = 100
GBM_BETAS = (0.0, 0.7)
GBM_EVALUATION_DAYS = 504
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_PATHS = 2000
EXT_FIELDS = {
    "ext_alt_hs300_dividend_yield": "dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
    "ext_alt_hs300_vol_of_vol_20d_low": "vol_of_vol_20d_low",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bytes_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = sidecar.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _load_module(name: str) -> Any:
    path = ROOT / "scripts" / f"{name}.py"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base() -> Any:
    return _load_module("research_r554_industry_neutral_confirmation")


def _write_immutable(path: Path, raw: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"immutable research artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def scientific_design() -> dict[str, Any]:
    base = _base()
    return {
        "question": (
            "Does the UI-selectable cash-safety strategy reproduce the frozen R552C "
            "candidate when using the built-in 14% volatility policy and generic industry cap?"
        ),
        "strategy_id": STRATEGY_ID,
        "policy_id": POLICY_ID,
        "factor_id": FACTOR_ID,
        "fixed_parameters": dict(FIXED_PARAMETERS),
        "windows": base.scientific_design()["windows"],
        "cost_multipliers": list(COSTS),
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.18,
            "validation_annual_positive": True,
            "stress_3x_annual_min": 0.08,
            "recent_return_positive": True,
            "fresh_positive_min": 3,
            "fresh_median_positive": True,
            "long_phase_positive_min": 6,
            "long_phase_median_positive": True,
            "reference_annual_delta_min": -0.03,
            "reference_sharpe_delta_min": -0.10,
            "five_x_hard_rejection": False,
        },
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": GBM_PATH_COUNT},
            "parameter_mc": {
                "required": False,
                "status": "fixed_frozen",
                "source_research_id": "R552C",
                "reason": "R552C selected p10_h60_v14 before this deployment-chain confirmation",
            },
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def _input_paths(root: Path) -> dict[str, Path]:
    inputs = {
        "r552_result": root / R552_RESULT.relative_to(ROOT),
        "r554_result": root / R554_RESULT.relative_to(ROOT),
        "r555_result": root / R555_RESULT.relative_to(ROOT),
        "r556_result": root / R556_RESULT.relative_to(ROOT),
        "strategy": root / STRATEGY_PATH.relative_to(ROOT),
        "factor": root / FACTOR_PATH.relative_to(ROOT),
        "matrix": root / "backend/app/backtest/matrix.py",
        "position_policies": root / "backend/app/backtest/position_policies.py",
    }
    inputs["r557_attempt_preregistration"] = root / R557_ATTEMPT_PREREG.relative_to(ROOT)
    inputs["r557_attempt_failure"] = root / R557_ATTEMPT_FAILURE.relative_to(ROOT)
    inputs["r557b_attempt_preregistration"] = root / R557B_ATTEMPT_PREREG.relative_to(ROOT)
    inputs["r557b_attempt_failure"] = root / R557B_ATTEMPT_FAILURE.relative_to(ROOT)
    return inputs


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R557 preregistration already exists: {PREREG}")
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": design["question"],
        "hypothesis": (
            "The registered factor, standard matrix strategy and built-in volatility policy "
            "will reproduce R552C closely enough to make the candidate directly backtestable."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": design,
        "sample": design["windows"],
        "execution": design["execution"],
        "gates": design["gates"],
        "falsification_checks": [
            "strategy_loads_as_matrix_native",
            "custom_factor_materialized_by_project_scoring",
            "generic_industry_cap_applied",
            "builtin_vol_target_policy_used",
            "formal_evidence_passed",
            "same_path_benchmark_and_100_path_gbm",
            "r552c_reference_delta_reported",
            "no_orders_or_active_version",
        ],
        "output_policy": design["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R557 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R557 input changed: {name}")


def _ready_panel(root: Path) -> tuple[pl.DataFrame, dict[str, Any]]:
    base = _base()
    _, runtime, candidate_panel = base._score_panel(root)
    ext = load_existing_ext_factors(DATA, EXT_FIELDS)
    panel = candidate_panel.join(ext, on=["symbol", "date"], how="left").sort(["date", "symbol"])
    panel = scoring.materialize_scoring_columns(panel, {FACTOR_ID: 1.0})
    return panel.select("symbol", "date", FACTOR_ID), runtime


def _config(
    p5: Any,
    runtime: dict[str, Any],
    *,
    start: date,
    end: date,
    cost_label: str,
    max_positions: int | None = None,
    max_hold_days: int | None = None,
) -> StrategyBacktestConfig:
    cost = runtime["r533"].cost_parameters(p5, cost_label)
    positions = max_positions or FIXED_PARAMETERS["max_positions"]
    hold = max_hold_days or FIXED_PARAMETERS["max_hold_days"]
    return StrategyBacktestConfig(
        strategy_id=STRATEGY_ID,
        symbols=list(runtime["symbols"]),
        start=start,
        end=end,
        params={
            "entry_score": FIXED_PARAMETERS["entry_score"],
            "exit_score": FIXED_PARAMETERS["exit_score"],
            "top_rank": FIXED_PARAMETERS["top_rank"],
            "max_industry_positions": FIXED_PARAMETERS["max_industry_positions"],
            "industry_by_symbol": runtime["industries"],
        },
        overrides={"stop_loss": FIXED_PARAMETERS["stop_loss"], "trailing_stop": None},
        matching="open_t+1",
        fees_pct=cost["fees_pct"],
        commission_pct=cost["commission_pct"],
        stamp_tax_pct=cost["stamp_tax_pct"],
        slippage_bps=cost["slippage_bps"],
        max_positions=positions,
        max_exposure_pct=1.0,
        initial_capital=1_000_000.0,
        position_sizing="policy",
        position_policy=POLICY_ID,
        position_policy_params={},
        mode="position",
        asset_type="stock",
        holding_days=hold,
        hs300=False,
    )


def _run_window(
    p5: Any,
    service: StrategyBacktestService,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    start: date,
    end: date,
    cost_label: str,
    label: str,
    prepared_cache: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    r533 = runtime["r533"]
    prepare_key = (str(start), str(end))
    prepared = prepared_cache.get(prepare_key)
    if prepared is None:
        seed = _config(p5, runtime, start=start, end=end, cost_label="2x")
        prepared_base = service.prepare_matrix_optimization([seed])
        prepared = p5._extend_prepared_with_panel(prepared_base, panel, [FACTOR_ID])
        prepared_cache[prepare_key] = prepared
    config = _config(p5, runtime, start=start, end=end, cost_label=cost_label)
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R557:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R557:{label}")
    if result.error:
        raise RuntimeError(f"R557:{label}: {result.error}")
    trades = [trade if isinstance(trade, dict) else service._trade_to_dict(trade) for trade in result.trades]
    return {
        "label": label,
        "variant": "ready_strategy",
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": r533._evidence_summary(evidence),
    }


def _synthetic_service() -> StrategyBacktestService:
    engine = StrategyEngine(strategy_dirs=[ROOT / "backend" / "app" / "strategy" / "research", DATA / "strategies" / "ai"])
    return StrategyBacktestService(BacktestEngine(None), engine)


def run_policy_path(
    spec: gbm.SyntheticPathSpec,
    *,
    scores: np.ndarray,
    score_name: str = FACTOR_ID,
    asset_count: int = gbm.ASSET_COUNT,
    warmup_days: int = gbm.WARMUP_DAYS,
    evaluation_days: int = gbm.EVALUATION_DAYS,
    max_positions: int = FIXED_PARAMETERS["max_positions"],
    max_hold_days: int = FIXED_PARAMETERS["max_hold_days"],
    top_rank: int = FIXED_PARAMETERS["top_rank"],
    industry_cap: int = FIXED_PARAMETERS["max_industry_positions"],
    symbols: tuple[str, ...] | None = None,
    industries: dict[str, str] | None = None,
    dates: tuple[date, ...] | None = None,
    service: StrategyBacktestService | None = None,
    cost_multipliers: tuple[int, ...] = COSTS,
) -> dict[str, Any]:
    market = gbm.generate_synthetic_market(
        spec,
        asset_count=asset_count,
        warmup_days=warmup_days,
        evaluation_days=evaluation_days,
        symbols=symbols,
        industries=industries,
        dates=dates,
    )
    market, resolved_name = gbm.with_fixed_score(market, scores=scores, score_name=score_name)
    base = gbm.build_project_market(market)
    service = service or _synthetic_service()
    params = {
        "entry_score": gbm.ENTRY_SCORE,
        "exit_score": gbm.EXIT_SCORE,
        "top_rank": top_rank,
        "max_industry_positions": industry_cap,
        "industry_by_symbol": market.industries,
    }
    benchmark_engine = BacktestEngine(None)
    benchmark_market = gbm.build_benchmark_market(market, base_market=base)
    years = evaluation_days / gbm.TRADING_DAYS_PER_YEAR
    benchmarks = {
        cost: gbm._simulation_metrics(
            benchmark_engine,
            benchmark_market,
            gbm.matcher_config(cost, stop_loss_pct=None, max_positions=asset_count, max_hold_days=None),
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
            start=market.dates[0],
            end=market.dates[-1],
            params=params,
            overrides={
                "max_hold_days": max_hold_days,
                "stop_loss": FIXED_PARAMETERS["stop_loss"],
                "trailing_stop": None,
                "basic_filter": _base()._synthetic_filter(),
            },
            matching="open_t+1",
            fees_pct=cost_params["fees_pct"],
            commission_pct=cost_params["commission_pct"],
            stamp_tax_pct=cost_params["stamp_tax_pct"],
            slippage_bps=cost_params["slippage_bps"],
            max_positions=max_positions,
            max_exposure_pct=1.0,
            initial_capital=1_000_000.0,
            position_sizing="policy",
            position_policy=POLICY_ID,
            position_policy_params={},
            mode="position",
            asset_type="stock",
            holding_days=max_hold_days,
            hs300=False,
        )

    prepared_config = make_config(cost_multipliers[0])
    prepared = service.prepare_matrix_optimization([prepared_config], market_data_override=base)
    for cost in cost_multipliers:
        config = make_config(cost)
        if prepared.signature != service._matrix_prepare_signature(config):
            raise RuntimeError("R557 prepared signature mismatch")
        result = service.run(config, prepared=prepared)
        if result.error:
            raise RuntimeError(f"R557 policy path failed: {result.error}")
        stats = dict(result.stats)
        benchmark = benchmarks[cost]
        observations.append({
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "cost_multiplier": cost,
            **{f"strategy_{key}": value for key, value in stats.items() if key in {"total_return", "annual_return", "sharpe", "max_drawdown", "avg_exposure", "max_exposure", "n_trades"}},
            **{f"benchmark_{key}": value for key, value in benchmark.items()},
            "excess_return": float(stats.get("total_return", 0.0)) - float(benchmark["total_return"]),
            "policy": POLICY_ID,
        })
    return {
        "signal_name": resolved_name,
        "policy": POLICY_ID,
        "path_key": {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id},
        "realized": {
            "close_sha256": bytes_sha256(market.close.tobytes()),
            "equal_weight_annual_return": market.realized_equal_weight_return,
            "equal_weight_annual_volatility": market.realized_equal_weight_volatility,
        },
        "benchmarks": [
            {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id, "cost_multiplier": cost, **benchmarks[cost]}
            for cost in cost_multipliers
        ],
        "observations": observations,
    }


def _run_gbm(root: Path) -> dict[str, Any]:
    base = _base()
    matrix, symbols, dates, industries = base._score_inputs(root)
    score_industries = {symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}") for index, symbol in enumerate(symbols)}
    service = _synthetic_service()
    grouped: dict[int, list[dict[str, Any]]] = {path_id: [] for path_id in range(GBM_PATH_COUNT)}
    for path_id in range(GBM_PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in GBM_BETAS:
                result = run_policy_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                    scores=matrix,
                    asset_count=300,
                    warmup_days=gbm.WARMUP_DAYS,
                    evaluation_days=GBM_EVALUATION_DAYS,
                    symbols=symbols,
                    industries=score_industries,
                    dates=dates,
                    service=service,
                )
                grouped[path_id].extend(result["observations"])
    paths = [{"path_id": path_id, "observations": rows} for path_id, rows in grouped.items()]
    replay_a = run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix,
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=GBM_EVALUATION_DAYS,
        symbols=symbols,
        industries=score_industries,
        dates=dates,
        service=service,
    )
    replay_b = run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix,
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=GBM_EVALUATION_DAYS,
        symbols=symbols,
        industries=score_industries,
        dates=dates,
        service=service,
    )
    deterministic = replay_a["realized"]["close_sha256"] == replay_b["realized"]["close_sha256"] and replay_a["observations"] == replay_b["observations"]
    summary = summarize_gbm_paths(paths, path_count=GBM_PATH_COUNT, scenarios=set(gbm.SCENARIOS), costs=COSTS, deterministic_replay=deterministic)
    if summary["constraints"]["errors"]:
        raise RuntimeError(f"R557 GBM constraints failed: {summary['constraints']['errors']}")
    return {
        "summary": summary,
        "path_observations_sha256": bytes_sha256(json.dumps(paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()),
        "scenario_parameters": {key: {"annual_drift": value.annual_drift, "annual_volatility": value.annual_volatility} for key, value in gbm.SCENARIOS.items()},
        "betas": list(GBM_BETAS),
        "cost_multipliers": list(COSTS),
        "evaluation_days": GBM_EVALUATION_DAYS,
    }


def _gate(
    *,
    full: dict[str, Any],
    full_3x: dict[str, Any],
    validation: dict[str, Any],
    recent: dict[str, Any],
    fresh: list[dict[str, Any]],
    long_phase: list[dict[str, Any]],
    reference_full: dict[str, float],
) -> dict[str, Any]:
    fresh_returns = [float(run["stats"].get("total_return") or 0.0) for run in fresh]
    phase_returns = [float(run["stats"].get("total_return") or 0.0) for run in long_phase]
    annual = float(full["stats"].get("annual_return") or 0.0)
    sharpe = float(full["stats"].get("sharpe") or 0.0)
    checks = {
        "full_annual_min": annual >= 0.10,
        "full_sharpe_min": sharpe >= 0.90,
        "full_mdd_min": float(full["stats"].get("max_drawdown") or 0.0) >= -0.18,
        "validation_annual_positive": float(validation["stats"].get("annual_return") or 0.0) > 0.0,
        "stress_3x_annual_min": float(full_3x["stats"].get("annual_return") or 0.0) >= 0.08,
        "recent_return_positive": float(recent["stats"].get("total_return") or 0.0) > 0.0,
        "fresh_positive_min": sum(value > 0.0 for value in fresh_returns) >= 3,
        "fresh_median_positive": bool(fresh_returns) and float(np.median(fresh_returns)) > 0.0,
        "long_phase_positive_min": sum(value > 0.0 for value in phase_returns) >= 6,
        "long_phase_median_positive": bool(phase_returns) and float(np.median(phase_returns)) > 0.0,
        "reference_annual_delta_min": annual - float(reference_full.get("annual_return") or 0.0) >= -0.03,
        "reference_sharpe_delta_min": sharpe - float(reference_full.get("sharpe") or 0.0) >= -0.10,
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
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    base = payload["reference"]
    ready = payload["full"]["2x"]
    lines = [
        "# R557 可直接回测的 cash_safety 正式确认",
        "",
        "> `project_formal`：标准策略 + 自定义因子 + 内置仓位策略 + 通用行业上限。",
        "",
        "## 配置",
        "",
        f"- 策略：`{STRATEGY_ID}`",
        f"- 因子：`{FACTOR_ID}`",
        f"- 仓位策略：`{POLICY_ID}`",
        f"- 参数：{FIXED_PARAMETERS}",
        "",
        "## 结果",
        "",
        "| 口径 | 全周期年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: |",
        f"| 可直接回测策略 | {ready['stats']['annual_return']:.2%} | {ready['stats']['sharpe']:.2f} | {ready['stats']['max_drawdown']:.2%} |",
        f"| R552C 参考 | {base['full']['annual_return']:.2%} | {base['full']['sharpe']:.2f} | {base['full']['max_drawdown']:.2%} |",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in payload["gate"]["checks"].items()]
    lines += ["", "## 边界", ""]
    lines += [f"- {item}" for item in payload["known_limits"]]
    lines.append("")
    return "\n".join(lines)


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    store.load_runtime_registry(DATA)
    panel, runtime = _ready_panel(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    base = _base()
    hs300 = base._load_hs300_returns(service)
    _, _, candidate_panel = base._score_panel(root)
    equal_weight = base._load_equal_weight_returns(candidate_panel)
    prepared_cache: dict[tuple[str, str], Any] = {}
    raw_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    public_cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    def run(start: date, end: date, cost: str) -> dict[str, Any]:
        key = (str(start), str(end), cost)
        if key not in public_cache:
            raw = _run_window(p5, service, runtime, panel, start=start, end=end, cost_label=cost, label=f"ready_{start}_{end}_{cost}", prepared_cache=prepared_cache)
            raw_cache[key] = raw
            public_cache[key] = base._public_run(raw, base._period_comparison(raw, hs300, equal_weight))
        return public_cache[key]

    full = {cost: run(base.START, base.END, cost) for cost in ("2x", "3x", "5x")}
    validation = run(base.VALIDATION_START, base.VALIDATION_END, "2x")
    recent = {cost: run(base.RECENT_START, base.END, cost) for cost in ("2x", "3x", "5x")}
    long_phase = [run(start, base.END, "2x") for start in base.LONG_PHASE_STARTS]
    fresh = [run(start, base.END, "2x") for start in base.FRESH_STARTS]
    bootstrap_windows = {}
    for name, (start, end) in (
        ("train", (base.START, base.TRAIN_END)),
        ("validation", (base.VALIDATION_START, base.VALIDATION_END)),
        ("recent", (base.RECENT_START, base.END)),
    ):
        if (str(start), str(end), "2x") not in raw_cache:
            run(start, end, "2x")
        bootstrap_windows[name] = bootstrap_equity_curve(
            raw_cache[(str(start), str(end), "2x")]["curve"],
            block_size=BOOTSTRAP_BLOCK,
            bootstrap_paths=BOOTSTRAP_PATHS,
            seed=55701 + len(bootstrap_windows),
        )
    bootstrap = {"method": "moving_block_bootstrap", "block_size": BOOTSTRAP_BLOCK, "bootstrap_paths": BOOTSTRAP_PATHS, "windows": bootstrap_windows}
    r552 = json.loads(R552_RESULT.read_text(encoding="utf-8"))
    reference = {
        "research_id": "R552C",
        "full": dict(r552["metrics"]["selected_full"]["stats"]),
        "validation": dict(r552["metrics"]["selected_validation"]["stats"]),
        "recent": dict(r552["metrics"]["selected_recent"]["2x"]["stats"]),
    }
    gate = _gate(full=full["2x"], full_3x=full["3x"], validation=validation, recent=recent["2x"], fresh=fresh, long_phase=long_phase, reference_full=reference["full"])
    gbm_result = _run_gbm(root)
    with tempfile.TemporaryDirectory(prefix="r557_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(gbm_result["summary"]["summary"], plot_dir / "r557_ready_cash_safety_gbm_distribution.png")
        constraints_plot = plot_gbm_constraints(gbm_result["summary"]["cost_summary"], plot_dir / "r557_ready_cash_safety_gbm_constraints.png")
        bootstrap_plot = plot_parameter_bootstrap(bootstrap, plot_dir / "r557_ready_cash_safety_bootstrap.png")
        artifacts = [
            {"kind": "gbm_distribution", "path": distribution.name, "sha256": sha256(distribution)},
            {"kind": "gbm_constraints", "path": constraints_plot.name, "sha256": sha256(constraints_plot)},
            {"kind": "parameter_bootstrap", "path": bootstrap_plot.name, "sha256": sha256(bootstrap_plot)},
        ]
        parameter_receipt = {
            "status": "fixed_frozen",
            "reason": "R552C selected p10_h60_v14 before the R557 deployment-chain confirmation",
            "source_research_id": "R552C",
            "selection_policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "bootstrap": bootstrap,
            "artifacts": [artifacts[-1]],
        }
        receipt = build_standard_validation_receipt(gbm_result["summary"], parameter_receipt, artifacts=artifacts)
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R557 standard validation failed: {checked['errors']}")
        conclusion = "watch_only" if gate["pass"] else "rejected"
        payload = {
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "design": scientific_design(),
            "deployment": {"strategy_id": STRATEGY_ID, "factor_id": FACTOR_ID, "policy_id": POLICY_ID, "parameters": FIXED_PARAMETERS},
            "full": full,
            "validation": validation,
            "recent": recent,
            "long_phase_starts": long_phase,
            "fresh_starts": fresh,
            "reference": reference,
            "gate": gate,
            "bootstrap": bootstrap,
            "gbm": gbm_result,
            "standard_validation": receipt,
            "conclusion": conclusion,
            "known_limits": [
                "The ready strategy uses a registered rank composite whose standalone validation IC was negative in R556B.",
                "Formal runs inject the research industry map; the UI fallback can use the current local industry map and therefore differ slightly.",
                "Recent windows have already been observed and are not independent out-of-sample data.",
                "GBM is a risk-mechanics null and cannot prove real financial alpha.",
                "No active version, orders, paper targets or production enablement are produced.",
            ],
            "negative_knowledge": [] if gate["pass"] else gate["failed"],
            "output_policy": scientific_design()["output_policy"],
        }
        attachments = [(distribution.name, distribution.read_bytes()), (constraints_plot.name, constraints_plot.read_bytes()), (bootstrap_plot.name, bootstrap_plot.read_bytes())]
        envelope = ResearchResultEnvelope.model_validate({
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "runner": RUNNER,
            "runner_sha256": sha256(Path(__file__).resolve()),
            "preregistration_sha256": verify_sidecar(PREREG),
            "generated_at": payload["generated_at"],
            "conclusion": payload["conclusion"],
            "gate_results": gate,
            "metrics": {
                "design": payload["design"],
                "deployment": payload["deployment"],
                "full": full,
                "validation": validation,
                "recent": recent,
                "long_phase_starts": long_phase,
                "fresh_starts": fresh,
                "reference": reference,
                "gate": gate,
                "bootstrap": bootstrap,
                "gbm": gbm_result,
                "standard_validation": receipt,
            },
            "source_result_hashes": [sha256(path) for path in _input_paths(root).values()],
            "formal_evidence": full["2x"]["formal_evidence"],
            "known_limits": payload["known_limits"],
            "negative_knowledge": payload["negative_knowledge"],
            "output_policy": payload["output_policy"],
        })
        published = publish_research_artifacts(DATA, preregistration_path=PREREG, result=envelope, markdown=_render_markdown(payload), operation="r557c_ready_cash_safety_confirmation_published", attachments=attachments).__dict__
    return {**payload, "published": published}


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
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R557 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({"conclusion": payload["conclusion"], "gate": payload["gate"], "full": payload["full"]["2x"]["stats"], "validation": payload["validation"]["stats"], "recent": payload["recent"]["2x"]["stats"], "published": payload["published"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
