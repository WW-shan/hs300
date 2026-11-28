#!/usr/bin/env python3
"""R554: formal HS300 confirmation of industry-neutral cash-safety scoring."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
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
from app.factor_research.standard_parameter_mc import bootstrap_equity_curve  # noqa: E402
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    plot_parameter_bootstrap,
    summarize_gbm_paths,
)
from app.strategy.engine import StrategyEngine  # noqa: E402

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R554"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r554_industry_neutral_confirmation_preregistration.json"
)
RUNNER = "scripts/research_r554_industry_neutral_confirmation.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R550_RESULT = RESEARCH / "r550_cash_safety_confirmation_result.json"
R552_RESULT = RESEARCH / "r552c_cash_safety_parameter_mc_result.json"
R553_RESULT = RESEARCH / "r553_r550_policy_gbm_stress_result.json"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
PREREG = RESEARCH / "r554_industry_neutral_confirmation_preregistration.json"
RESULT = RESEARCH / "r554_industry_neutral_confirmation_result.json"
MARKDOWN = RESEARCH / "r554_industry_neutral_confirmation_result.md"
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
PRIMARY_VARIANT = "industry_neutral"
FIXED_PARAMETERS = {
    "max_positions": 10,
    "max_hold_days": 60,
    "vol_target": 0.14,
    "stop_loss": -0.12,
    "industry_cap": 3,
}
FIXED_ADDONS = ("cf_r547_cash_quality", "cf_r547_value_quality_safety")
LONG_PHASE_STARTS = (
    date(2022, 5, 5),
    date(2022, 11, 1),
    date(2023, 5, 4),
    date(2023, 11, 1),
    date(2024, 5, 6),
    date(2024, 11, 1),
    date(2025, 1, 2),
    date(2025, 5, 6),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
FRESH_STARTS = (
    date(2025, 10, 14),
    date(2026, 1, 5),
    date(2026, 3, 2),
    date(2026, 5, 6),
    date(2026, 7, 1),
)
GBM_PATH_COUNT = 100
GBM_BETAS = (0.0, 0.7)
GBM_COSTS = (2, 3, 5)
GBM_EVALUATION_DAYS = 504
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_PATHS = 2000


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


def variants() -> dict[str, dict[str, Any] | None]:
    return {
        "control": None,
        "industry_neutral": {
            "industry": "industry_l1",
            "styles": [],
            "shrink": 1.0,
        },
    }


def _policy_variant() -> dict[str, Any]:
    return {
        "id": "vol_14",
        "kind": "vol_target",
        "target": FIXED_PARAMETERS["vol_target"],
        "lookback": 60,
        "floor": 0.50,
        "order": 14,
    }


def scientific_design() -> dict[str, Any]:
    return {
        "question": "Does industry-neutral scoring improve the frozen R552C cash-safety portfolio?",
        "fixed_variant": "cash_safety",
        "primary_variant": PRIMARY_VARIANT,
        "control_variant": "control",
        "variants": variants(),
        "fixed_parameters": dict(FIXED_PARAMETERS),
        "fixed_policy": _policy_variant(),
        "fixed_addons": list(FIXED_ADDONS),
        "neutralization": {
            "industry": "industry_l1",
            "styles": [],
            "shrink": 1.0,
            "selection": "fixed_before_run_from_user_neutralization_requirement",
            "recent_used_for_selection": False,
        },
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "long_phase_starts": [str(value) for value in LONG_PHASE_STARTS],
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
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
            "validation_beats_control": True,
            "stress_3x_annual_min": 0.08,
            "recent_return_positive": True,
            "fresh_positive_min": 3,
            "fresh_median_positive": True,
            "long_phase_positive_min": 6,
            "long_phase_median_positive": True,
            "five_x_hard_rejection": False,
        },
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": GBM_PATH_COUNT},
            "parameter_mc": {
                "required": False,
                "status": "fixed_frozen",
                "source_research_id": "R552C",
                "reason": "R552C selected p10_h60_v14 before this neutralization confirmation",
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
        "r550_result": root / R550_RESULT.relative_to(ROOT),
        "r552_result": root / R552_RESULT.relative_to(ROOT),
        "r553_result": root / R553_RESULT.relative_to(ROOT),
        "r547_result": root / R547_RESULT.relative_to(ROOT),
    }
    for path in sorted((root / "data" / "factor_research" / "candidates" / "r547").glob("*.json")):
        inputs[f"candidate_{path.stem}"] = path
    return inputs


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R554 preregistration already exists: {PREREG}")
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": scientific_design()["question"],
        "hypothesis": (
            "Removing the cross-industry level from the frozen cash-safety score "
            "will reduce concentration and improve validation robustness without leverage."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": scientific_design(),
        "sample": scientific_design()["windows"],
        "execution": scientific_design()["execution"],
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "primary_variant_frozen_before_run",
            "recent_not_used_for_variant_selection",
            "industry_neutralization_applied_in_matrix_pipeline",
            "same_factor_score_and_parameters_as_control",
            "benchmark_is_project_000300_sh",
            "equal_weight_hs300_reported_separately",
            "gbm_100_paths_2_betas_4_scenarios_2_3_5_costs",
            "moving_block_bootstrap_from_real_equity_curves",
            "no_orders_or_active_version",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(PREREG, raw)
    return PREREG


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R554 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R554 input changed: {name}")


def _score_panel(root: Path) -> tuple[pl.DataFrame, dict[str, Any], pl.DataFrame]:
    r542 = _load_module("research_r542_recent_failure_attribution")
    r547 = _load_module("research_r547_incremental_factor_screen")
    r549 = _load_module("research_r549_quality_addon_portfolio")
    runtime = r542.load_runtime(root)
    membership = r547._load_membership(root)
    candidate_panel = r547._load_price_panel(root, membership)
    candidate_panel = r547._add_candidate_sources(
        candidate_panel,
        r547.load_candidate_definitions(root),
    )
    panel = r549.build_variant_panel(runtime, candidate_panel, FIXED_ADDONS)
    return panel, runtime, candidate_panel


def _curve_returns(curve: list[dict[str, Any]]) -> dict[str, float]:
    output: dict[str, float] = {}
    for index in range(1, len(curve)):
        previous = float(curve[index - 1]["value"])
        current = float(curve[index]["value"])
        if previous > 0.0:
            output[str(curve[index]["date"])[:10]] = current / previous - 1.0
    return output


def _period_return(returns: dict[str, float], start: date, end: date) -> float:
    days = [day for day in sorted(returns) if start < date.fromisoformat(day) <= end]
    return round(float(math.prod(1.0 + returns[day] for day in days) - 1.0), 8)


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "n": int(array.size),
        "p05": round(float(np.quantile(array, 0.05)), 8),
        "p50": round(float(np.quantile(array, 0.50)), 8),
        "p95": round(float(np.quantile(array, 0.95)), 8),
        "mean": round(float(array.mean()), 8),
        "positive_ratio": round(float(np.mean(array > 0.0)), 6),
    }


def _rolling_summary(
    curve: list[dict[str, Any]],
    hs300: dict[str, float],
    *,
    window: int = 252,
) -> dict[str, Any]:
    strategy = _curve_returns(curve)
    days = sorted(strategy)
    if len(days) <= window:
        return {"window": window, "count": 0}
    values: list[float] = []
    excess: list[float] = []
    for index in range(window, len(days)):
        current = days[index]
        prior = days[index - window]
        strategy_return = _period_return(strategy, date.fromisoformat(prior), date.fromisoformat(current))
        benchmark_return = _period_return(hs300, date.fromisoformat(prior), date.fromisoformat(current))
        values.append(strategy_return)
        excess.append(strategy_return - benchmark_return)
    return {
        "window": window,
        "strategy": _distribution(values),
        "excess_hs300": _distribution(excess),
    }


def _yearly_summary(curve: list[dict[str, Any]], hs300: dict[str, float]) -> list[dict[str, Any]]:
    strategy = _curve_returns(curve)
    output = []
    for year in sorted({day[:4] for day in strategy}):
        year_start = date(int(year), 1, 1)
        year_end = date(int(year), 12, 31)
        strategy_return = _period_return(strategy, year_start, year_end)
        benchmark_return = _period_return(hs300, year_start, year_end)
        output.append({
            "year": year,
            "strategy_return": strategy_return,
            "hs300_return": benchmark_return,
            "excess_hs300": round(strategy_return - benchmark_return, 8),
        })
    return output


def _period_comparison(
    run: dict[str, Any],
    hs300: dict[str, float],
    equal_weight: dict[str, float],
) -> dict[str, Any]:
    returns = _curve_returns(run["curve"])
    start = date.fromisoformat(run["start"])
    end = date.fromisoformat(run["end"])
    strategy_return = float(run["stats"].get("total_return") or 0.0)
    hs_return = _period_return(hs300, start, end)
    ew_return = _period_return(equal_weight, start, end)
    return {
        "strategy_return": round(strategy_return, 8),
        "hs300_return": hs_return,
        "equal_weight_hs300_return": ew_return,
        "excess_hs300": round(strategy_return - hs_return, 8),
        "excess_equal_weight": round(strategy_return - ew_return, 8),
        "rolling_252d": _rolling_summary(run["curve"], hs300),
        "yearly": _yearly_summary(run["curve"], hs300),
        "curve_sha256": bytes_sha256(
            json.dumps(run["curve"], sort_keys=True, separators=(",", ":")).encode()
        ),
        "returns_observations": len(returns),
    }


def _run_window(
    root: Path,
    p5: Any,
    service: StrategyBacktestService,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    variant_id: str,
    start: date,
    end: date,
    cost_label: str,
    label: str,
    prepared_cache: dict[tuple[str, str], Any] | None = None,
) -> dict[str, Any]:
    r545 = _load_module("research_r545_stable7_risk_budget")
    r533 = runtime["r533"]
    prepare_key = (str(start), str(end))
    prepared = (prepared_cache or {}).get(prepare_key)
    if prepared is None:
        prepared = r545.prepare_panel(
            p5,
            service,
            runtime,
            panel,
            start=start,
            end=end,
        )
        if prepared_cache is not None:
            prepared_cache[prepare_key] = prepared
    config: StrategyBacktestConfig = r533.build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=FIXED_PARAMETERS["max_positions"],
        max_hold_days=FIXED_PARAMETERS["max_hold_days"],
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": FIXED_PARAMETERS["stop_loss"],
        "trailing_stop": None,
    }
    neutralize = variants()[variant_id]
    if neutralize is not None:
        config.overrides = {**config.overrides, "neutralize": neutralize}
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": FIXED_PARAMETERS["industry_cap"],
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "policy"
    config.position_policy = _policy_variant()["id"]
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R554:{label}: prepared signature mismatch")
    with r545.policy_injection(_policy_variant()):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R554:{label}")
        if result.error:
            raise RuntimeError(f"R554:{label}: {result.error}")
        trades = [
            trade if isinstance(trade, dict) else service._trade_to_dict(trade)
            for trade in result.trades
        ]
    return {
        "label": label,
        "variant": variant_id,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": r533._evidence_summary(evidence),
    }


def _public_run(run: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "variant": run["variant"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
        "comparison": comparison,
    }


def _synthetic_service() -> StrategyBacktestService:
    engine = StrategyEngine(
        strategy_dirs=[ROOT / "backend" / "app" / "strategy" / "research"]
    )
    return StrategyBacktestService(BacktestEngine(None), engine)


def _synthetic_filter() -> dict[str, Any]:
    return {
        "price_min": 0.0,
        "price_max": 1_000_000.0,
        "market_cap_min": 0.0,
        "float_cap_min": None,
        "float_cap_max": None,
        "amount_min": 0.0,
        "amount_max": None,
        "turnover_min": None,
        "turnover_max": None,
        "exclude_st": False,
        "exclude_new_days": 0,
        "boards": [],
    }


def run_policy_path(
    spec: gbm.SyntheticPathSpec,
    *,
    scores: np.ndarray,
    neutralize: dict[str, Any] | None,
    score_name: str = "r550_cash_safety",
    asset_count: int = gbm.ASSET_COUNT,
    warmup_days: int = gbm.WARMUP_DAYS,
    evaluation_days: int = gbm.EVALUATION_DAYS,
    max_positions: int = FIXED_PARAMETERS["max_positions"],
    max_hold_days: int = FIXED_PARAMETERS["max_hold_days"],
    top_rank: int = 20,
    industry_cap: int = FIXED_PARAMETERS["industry_cap"],
    symbols: tuple[str, ...] | None = None,
    industries: dict[str, str] | None = None,
    dates: tuple[date, ...] | None = None,
    service: StrategyBacktestService | None = None,
    cost_multipliers: tuple[int, ...] = GBM_COSTS,
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
    strategy = service.strategy_engine.get("factor_rank_research")
    if strategy.execution_backend != "matrix_native":
        raise RuntimeError("R554 requires the factor_rank_research matrix strategy")
    params = {
        "scoring": {resolved_name: 1.0},
        "directions": {resolved_name: "high"},
        "entry_score": gbm.ENTRY_SCORE,
        "exit_score": gbm.EXIT_SCORE,
        "top_rank": top_rank,
        "industry_by_symbol": market.industries,
        "max_industry_positions": industry_cap,
    }
    observations: list[dict[str, Any]] = []
    benchmark_engine = BacktestEngine(None)
    benchmark_market = gbm.build_benchmark_market(market, base_market=base)
    years = evaluation_days / gbm.TRADING_DAYS_PER_YEAR
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

    def make_config(cost: int) -> StrategyBacktestConfig:
        cost_params = gbm.cost_parameters(cost)
        overrides: dict[str, Any] = {
            "max_hold_days": max_hold_days,
            "stop_loss": FIXED_PARAMETERS["stop_loss"],
            "trailing_stop": None,
            "basic_filter": _synthetic_filter(),
        }
        if neutralize is not None:
            overrides["neutralize"] = neutralize
        return StrategyBacktestConfig(
            strategy_id="factor_rank_research",
            symbols=list(market.symbols),
            start=market.dates[0],
            end=market.dates[-1],
            params=params,
            overrides=overrides,
            matching="open_t+1",
            fees_pct=cost_params["fees_pct"],
            commission_pct=cost_params["commission_pct"],
            stamp_tax_pct=cost_params["stamp_tax_pct"],
            slippage_bps=cost_params["slippage_bps"],
            max_positions=max_positions,
            max_exposure_pct=1.0,
            initial_capital=1_000_000.0,
            position_sizing="policy",
            position_policy=_policy_variant()["id"],
            position_policy_params={},
            mode="position",
            asset_type="stock",
            holding_days=max_hold_days,
            hs300=False,
        )

    prepared_config = make_config(cost_multipliers[0])
    prepared = service.prepare_matrix_optimization([prepared_config], market_data_override=base)
    r545 = _load_module("research_r545_stable7_risk_budget")
    for cost in cost_multipliers:
        config = make_config(cost)
        if prepared.signature != service._matrix_prepare_signature(config):
            raise RuntimeError("R554 prepared signature mismatch")
        with r545.policy_injection(_policy_variant()):
            result = service.run(config, prepared=prepared)
        if result.error:
            raise RuntimeError(f"R554 policy path failed: {result.error}")
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
            "policy": _policy_variant()["id"],
            "variant": PRIMARY_VARIANT if neutralize is not None else "control",
        })
    return {
        "signal_name": resolved_name,
        "policy": _policy_variant()["id"],
        "path_key": {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id},
        "realized": {
            "close_sha256": bytes_sha256(market.close.tobytes()),
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


def _score_inputs(root: Path) -> tuple[np.ndarray, tuple[str, ...], tuple[date, ...], dict[str, str]]:
    r551 = _load_module("research_r551_r550_gbm_stress")
    panel, industries = r551._score_panel(root)
    dates = r551._target_dates(panel)
    symbols = r551._target_symbols(panel, dates)
    matrix, symbols, dates = r551.build_frozen_score_matrix(
        panel,
        target_dates=dates,
        target_symbols=symbols,
    )
    return matrix, symbols, dates, industries


def _run_gbm(root: Path) -> dict[str, Any]:
    matrix, symbols, dates, industries = _score_inputs(root)
    score_industries = {
        symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}")
        for index, symbol in enumerate(symbols)
    }
    service = _synthetic_service()
    grouped: dict[int, list[dict[str, Any]]] = {path_id: [] for path_id in range(GBM_PATH_COUNT)}
    for path_id in range(GBM_PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in GBM_BETAS:
                result = run_policy_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                    scores=matrix,
                    neutralize=variants()[PRIMARY_VARIANT],
                    asset_count=300,
                    warmup_days=gbm.WARMUP_DAYS,
                    evaluation_days=GBM_EVALUATION_DAYS,
                    max_positions=FIXED_PARAMETERS["max_positions"],
                    max_hold_days=FIXED_PARAMETERS["max_hold_days"],
                    top_rank=20,
                    industry_cap=FIXED_PARAMETERS["industry_cap"],
                    symbols=symbols,
                    industries=score_industries,
                    dates=dates,
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
        neutralize=variants()[PRIMARY_VARIANT],
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=GBM_EVALUATION_DAYS,
        max_positions=FIXED_PARAMETERS["max_positions"],
        max_hold_days=FIXED_PARAMETERS["max_hold_days"],
        top_rank=20,
        industry_cap=FIXED_PARAMETERS["industry_cap"],
        symbols=symbols,
        industries=score_industries,
        dates=dates,
        service=service,
    )
    replay_b = run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix,
        neutralize=variants()[PRIMARY_VARIANT],
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=GBM_EVALUATION_DAYS,
        max_positions=FIXED_PARAMETERS["max_positions"],
        max_hold_days=FIXED_PARAMETERS["max_hold_days"],
        top_rank=20,
        industry_cap=FIXED_PARAMETERS["industry_cap"],
        symbols=symbols,
        industries=score_industries,
        dates=dates,
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
        costs=GBM_COSTS,
        deterministic_replay=deterministic,
    )
    if summary["constraints"]["errors"]:
        raise RuntimeError(f"R554 GBM constraints failed: {summary['constraints']['errors']}")
    return {
        "summary": summary,
        "path_observations_sha256": bytes_sha256(
            json.dumps(paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ),
        "scenario_parameters": {
            key: {
                "annual_drift": value.annual_drift,
                "annual_volatility": value.annual_volatility,
            }
            for key, value in gbm.SCENARIOS.items()
        },
        "betas": list(GBM_BETAS),
        "cost_multipliers": list(GBM_COSTS),
        "evaluation_days": GBM_EVALUATION_DAYS,
    }


def _load_hs300_returns(service: StrategyBacktestService) -> dict[str, float]:
    frame = service.engine.repo.get_index_daily(
        "000300.SH",
        START,
        END,
        columns=["date", "close"],
    )
    frame = (
        frame.filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
        .sort("date")
    )
    dates = [str(value)[:10] for value in frame["date"].to_list()]
    closes = [float(value) for value in frame["close"].to_list()]
    return {
        dates[index]: closes[index] / closes[index - 1] - 1.0
        for index in range(1, len(dates))
    }


def _load_equal_weight_returns(candidate_panel: pl.DataFrame) -> dict[str, float]:
    frame = (
        candidate_panel.select(["symbol", "date", "close"])
        .sort(["symbol", "date"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0).alias("return")
        )
        .filter(pl.col("return").is_not_null() & pl.col("return").is_finite())
        .group_by("date")
        .agg(pl.col("return").mean().alias("return"))
        .sort("date")
    )
    return {
        str(day)[:10]: float(value)
        for day, value in zip(frame["date"].to_list(), frame["return"].to_list(), strict=True)
    }


def _gate(
    *,
    full: dict[str, Any],
    full_3x: dict[str, Any],
    validation: dict[str, Any],
    control_validation: dict[str, Any],
    recent: dict[str, Any],
    fresh: list[dict[str, Any]],
    long_phase: list[dict[str, Any]],
) -> dict[str, Any]:
    fresh_returns = [float(run["stats"].get("total_return") or 0.0) for run in fresh]
    phase_returns = [float(run["stats"].get("total_return") or 0.0) for run in long_phase]
    checks = {
        "full_annual_min": float(full["stats"].get("annual_return") or 0.0) >= 0.10,
        "full_sharpe_min": float(full["stats"].get("sharpe") or 0.0) >= 0.90,
        "full_mdd_min": float(full["stats"].get("max_drawdown") or 0.0) >= -0.18,
        "validation_annual_positive": float(validation["stats"].get("annual_return") or 0.0) > 0.0,
        "validation_beats_control": float(validation["stats"].get("annual_return") or 0.0)
        > float(control_validation["stats"].get("annual_return") or 0.0),
        "stress_3x_annual_min": float(full_3x["stats"].get("annual_return") or 0.0) >= 0.08,
        "recent_return_positive": float(recent["stats"].get("total_return") or 0.0) > 0.0,
        "fresh_positive_min": sum(value > 0.0 for value in fresh_returns) >= 3,
        "fresh_median_positive": bool(fresh_returns) and float(np.median(fresh_returns)) > 0.0,
        "long_phase_positive_min": sum(value > 0.0 for value in phase_returns) >= 6,
        "long_phase_median_positive": bool(phase_returns) and float(np.median(phase_returns)) > 0.0,
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
    candidate = payload["candidate"]
    control = payload["control"]
    gate = payload["gate"]
    lines = [
        "# R554 行业中性 cash_safety 正式确认",
        "",
        "> 固定 R552C 参数，在项目 HS300 数据链上比较原始评分与行业中性评分；近期仅披露，不参与变体选择。",
        "",
        "## 固定参数",
        "",
        f"- 持仓：{FIXED_PARAMETERS['max_positions']}",
        f"- 持有：{FIXED_PARAMETERS['max_hold_days']} 日",
        f"- 波动目标：{FIXED_PARAMETERS['vol_target']:.0%}",
        f"- 止损：{FIXED_PARAMETERS['stop_loss']:.0%}",
        f"- 行业上限：{FIXED_PARAMETERS['industry_cap']}",
        "",
        "## 结果",
        "",
        "| 口径 | 区间 | 收益 | 年化 | Sharpe | MDD | 交易 | 超额 HS300 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, run in (
        ("行业中性", candidate["full_2x"]),
        ("原始对照", control["full_2x"]),
        ("行业中性", candidate["validation"]),
        ("原始对照", control["validation"]),
        ("行业中性", candidate["recent_2x"]),
        ("原始对照", control["recent_2x"]),
    ):
        stats = run["stats"]
        lines.append(
            f"| {label} | {run['start']} → {run['end']} | "
            f"{stats['total_return']:.2%} | {stats['annual_return']:.2%} | "
            f"{stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} | "
            f"{run['trade_count']} | {run['comparison']['excess_hs300']:.2%} |"
        )
    lines += [
        "",
        "## 稳健性",
        "",
        f"- 新鲜起点：{gate['fresh_positive_count']}/{len(FRESH_STARTS)} 为正，中位 {gate['fresh_median']:.2%}。",
        f"- 长阶段起点：{gate['long_phase_positive_count']}/{len(LONG_PHASE_STARTS)} 为正，中位 {gate['long_phase_median']:.2%}。",
        f"- 3x 全周期年化：{candidate['full_3x']['stats']['annual_return']:.2%}。",
        f"- 5x 近期年化：{candidate['recent_5x']['stats']['annual_return']:.2%}（仅警告）。",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in gate["checks"].items()]
    lines += [
        "",
        "## GBM",
        "",
        f"- 路径数：{payload['gbm']['summary']['path_count']}",
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
    panel, runtime, candidate_panel = _score_panel(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    hs300 = _load_hs300_returns(service)
    equal_weight = _load_equal_weight_returns(candidate_panel)

    comparisons: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    prepared_cache: dict[tuple[str, str], Any] = {}
    raw_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def run(variant_id: str, start: date, end: date, cost: str) -> dict[str, Any]:
        key = (variant_id, str(start), str(end), cost)
        if key not in comparisons:
            raw = _run_window(
                root,
                p5,
                service,
                runtime,
                panel,
                variant_id=variant_id,
                start=start,
                end=end,
                cost_label=cost,
                label=f"{variant_id}_{start}_{end}_{cost}",
                prepared_cache=prepared_cache,
            )
            raw_cache[key] = raw
            comparisons[key] = _public_run(raw, _period_comparison(raw, hs300, equal_weight))
        return comparisons[key]

    candidate_train = run(PRIMARY_VARIANT, START, TRAIN_END, "2x")
    candidate_validation = run(PRIMARY_VARIANT, VALIDATION_START, VALIDATION_END, "2x")
    control_validation = run("control", VALIDATION_START, VALIDATION_END, "2x")
    candidate_full = {
        cost: run(PRIMARY_VARIANT, START, END, cost)
        for cost in ("2x", "3x", "5x")
    }
    control_full = {cost: run("control", START, END, cost) for cost in ("2x", "3x", "5x")}
    candidate_recent = {
        cost: run(PRIMARY_VARIANT, RECENT_START, END, cost)
        for cost in ("2x", "3x", "5x")
    }
    control_recent = {cost: run("control", RECENT_START, END, cost) for cost in ("2x", "3x", "5x")}
    long_phase = [
        run(PRIMARY_VARIANT, start, END, "2x")
        for start in LONG_PHASE_STARTS
    ]
    fresh = [
        run(PRIMARY_VARIANT, start, END, "2x")
        for start in FRESH_STARTS
    ]

    bootstrap_curves = {}
    for name, key in (
        ("train", (PRIMARY_VARIANT, str(START), str(TRAIN_END), "2x")),
        ("validation", (PRIMARY_VARIANT, str(VALIDATION_START), str(VALIDATION_END), "2x")),
        ("recent", (PRIMARY_VARIANT, str(RECENT_START), str(END), "2x")),
    ):
        raw = raw_cache[key]
        bootstrap_curves[name] = bootstrap_equity_curve(
            raw["curve"],
            block_size=BOOTSTRAP_BLOCK,
            bootstrap_paths=BOOTSTRAP_PATHS,
            seed=55401 + len(bootstrap_curves),
        )
    bootstrap = {
        "method": "moving_block_bootstrap",
        "block_size": BOOTSTRAP_BLOCK,
        "bootstrap_paths": BOOTSTRAP_PATHS,
        "windows": bootstrap_curves,
    }

    gate = _gate(
        full=candidate_full["2x"],
        full_3x=candidate_full["3x"],
        validation=candidate_validation,
        control_validation=control_validation,
        recent=candidate_recent["2x"],
        fresh=fresh,
        long_phase=long_phase,
    )

    gbm_result = _run_gbm(root)
    with tempfile.TemporaryDirectory(prefix="r554_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(
            gbm_result["summary"]["summary"],
            plot_dir / "r554_industry_neutral_gbm_distribution.png",
        )
        constraints_plot = plot_gbm_constraints(
            gbm_result["summary"]["cost_summary"],
            plot_dir / "r554_industry_neutral_gbm_constraints.png",
        )
        bootstrap_plot = plot_parameter_bootstrap(
            bootstrap,
            plot_dir / "r554_industry_neutral_parameter_bootstrap.png",
        )
        artifacts = [
            {"kind": "gbm_distribution", "path": distribution.name, "sha256": sha256(distribution)},
            {"kind": "gbm_constraints", "path": constraints_plot.name, "sha256": sha256(constraints_plot)},
            {"kind": "parameter_bootstrap", "path": bootstrap_plot.name, "sha256": sha256(bootstrap_plot)},
        ]
        parameter_receipt = {
            "status": "fixed_frozen",
            "reason": "R552C selected p10_h60_v14 before R554",
            "source_research_id": "R552C",
            "selection_policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "bootstrap": bootstrap,
            "artifacts": [artifacts[-1]],
        }
        receipt = build_standard_validation_receipt(
            gbm_result["summary"],
            parameter_receipt,
            artifacts=artifacts,
        )
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R554 standard validation failed: {checked['errors']}")
        conclusion = "watch_only" if gate["pass"] else "rejected"
        payload = {
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "design": scientific_design(),
            "candidate": {
                "train_2x": candidate_train,
                "validation": candidate_validation,
                "full_2x": candidate_full["2x"],
                "full_3x": candidate_full["3x"],
                "full_5x": candidate_full["5x"],
                "recent_2x": candidate_recent["2x"],
                "recent_3x": candidate_recent["3x"],
                "recent_5x": candidate_recent["5x"],
                "long_phase_starts": long_phase,
                "fresh_starts": fresh,
            },
            "control": {
                "validation": control_validation,
                "full_2x": control_full["2x"],
                "full_3x": control_full["3x"],
                "full_5x": control_full["5x"],
                "recent_2x": control_recent["2x"],
                "recent_3x": control_recent["3x"],
                "recent_5x": control_recent["5x"],
            },
            "gate": gate,
            "bootstrap": bootstrap,
            "gbm": gbm_result,
            "standard_validation": receipt,
            "conclusion": conclusion,
            "known_limits": [
                "Recent starts have already been observed in R550/R552C and are not independent OOS data.",
                "The strategy is still watch-only, with active_version=null and no orders or paper targets.",
                "Industry labels are the project PIT industry mapping; classification errors remain a data risk.",
                "GBM prices contain no financial signal, so GBM can validate risk mechanics but cannot prove real alpha.",
                "5x cost is warning-only; 2x is the baseline and 3x is the stress gate.",
            ],
            "negative_knowledge": [] if gate["pass"] else gate["failed"],
            "output_policy": scientific_design()["output_policy"],
        }
        attachments = [
            (distribution.name, distribution.read_bytes()),
            (constraints_plot.name, constraints_plot.read_bytes()),
            (bootstrap_plot.name, bootstrap_plot.read_bytes()),
        ]
        prerequisites = verify_sidecar(PREREG)
        result_envelope = ResearchResultEnvelope.model_validate({
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "runner": RUNNER,
            "runner_sha256": sha256(Path(__file__).resolve()),
            "preregistration_sha256": prerequisites,
            "generated_at": payload["generated_at"],
            "conclusion": payload["conclusion"],
            "gate_results": payload["gate"],
            "metrics": {
                "design": payload["design"],
                "candidate": payload["candidate"],
                "control": payload["control"],
                "gate": payload["gate"],
                "bootstrap": payload["bootstrap"],
                "gbm": payload["gbm"],
                "standard_validation": payload["standard_validation"],
            },
            "source_result_hashes": [sha256(path) for path in _input_paths(root).values()],
            "formal_evidence": candidate_full["2x"]["formal_evidence"],
            "known_limits": payload["known_limits"],
            "negative_knowledge": payload["negative_knowledge"],
            "output_policy": payload["output_policy"],
        })
        published = publish_research_artifacts(
            DATA,
            preregistration_path=PREREG,
            result=result_envelope,
            markdown=_render_markdown(payload),
            operation="r554_industry_neutral_confirmation_published",
            attachments=attachments,
        ).__dict__
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
        raise FileExistsError("R554 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "gate": payload["gate"],
        "full": payload["candidate"]["full_2x"]["stats"],
        "validation": payload["candidate"]["validation"]["stats"],
        "recent": payload["candidate"]["recent_2x"]["stats"],
        "published": payload["published"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
