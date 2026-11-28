#!/usr/bin/env python3
"""R558: train/validation parameter Monte Carlo for the ready cash-safety strategy."""

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

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R558"
RESEARCH_PREREGISTRATION = "data/factor_research/research/r558_ready_parameter_mc_preregistration.json"
RUNNER = "scripts/research_r558_ready_parameter_mc.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = RESEARCH / "r558_ready_parameter_mc_preregistration.json"
RESULT = RESEARCH / "r558_ready_parameter_mc_result.json"
MARKDOWN = RESEARCH / "r558_ready_parameter_mc_result.md"
R552_RESULT = RESEARCH / "r552c_cash_safety_parameter_mc_result.json"
R557_RESULT = RESEARCH / "r557c_ready_cash_safety_confirmation_result.json"
STRATEGY_PATH = DATA / "strategies" / "ai" / "ai_hs300_cash_safety_v14.py"
FACTOR_PATH = DATA / "user_data" / "custom_factors" / "uf_r550_cash_safety_rank.json"
ENTRY_SCORES = (60.0, 70.0, 80.0)
TOP_RANKS = (10, 20, 30)
INDUSTRY_CAPS = (2, 3, 5)
FIXED = {"max_positions": 10, "max_hold_days": 60, "stop_loss": -0.12}
CENTER = {"entry_score": 70.0, "top_rank": 20, "max_industry_positions": 3}
COSTS = (2, 3, 5)
GBM_PATH_COUNT = 100
GBM_BETAS = (0.0, 0.7)
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


def _r557() -> Any:
    return _load_module("research_r557_ready_cash_safety_confirmation")


def _r554() -> Any:
    return _load_module("research_r554_industry_neutral_confirmation")


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


def parameter_grid() -> list[dict[str, Any]]:
    return [
        {
            "parameter": f"e{round(entry)}_t{top}_c{cap}",
            "params": {
                "entry_score": entry,
                "top_rank": top,
                "max_industry_positions": cap,
            },
        }
        for entry in ENTRY_SCORES
        for top in TOP_RANKS
        for cap in INDUSTRY_CAPS
    ]


def scientific_design() -> dict[str, Any]:
    r557 = _r557()
    windows = r557.scientific_design()["windows"]
    return {
        "question": "Which ready-strategy entry/rank/industry-cap parameters pass train and validation without using recent data?",
        "strategy_id": r557.STRATEGY_ID,
        "factor_id": r557.FACTOR_ID,
        "policy_id": r557.POLICY_ID,
        "fixed": dict(FIXED),
        "grid": {
            "entry_score": list(ENTRY_SCORES),
            "top_rank": list(TOP_RANKS),
            "max_industry_positions": list(INDUSTRY_CAPS),
        },
        "grid_size": len(parameter_grid()),
        "center": dict(CENTER),
        "windows": windows,
        "selection": {
            "policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "ranking": "maximize min(train annual, validation annual) after hard constraints",
        },
        "gates": {
            "min_train_annual": 0.08,
            "max_mdd": -0.20,
            "min_trades": 20,
            "validation_positive": True,
            "plateau_required": True,
            "cost_monotonic_required": True,
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.18,
            "stress_3x_annual_min": 0.08,
            "recent_return_positive": True,
            "fresh_positive_min": 3,
            "fresh_median_positive": True,
            "long_phase_positive_min": 6,
            "long_phase_median_positive": True,
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
    return {
        "r552_result": root / R552_RESULT.relative_to(ROOT),
        "r557_result": root / R557_RESULT.relative_to(ROOT),
        "strategy": root / STRATEGY_PATH.relative_to(ROOT),
        "factor": root / FACTOR_PATH.relative_to(ROOT),
        "matrix": root / "backend/app/backtest/matrix.py",
        "position_policies": root / "backend/app/backtest/position_policies.py",
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R558 preregistration already exists: {PREREG}")
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": design["question"],
        "hypothesis": "A wider entry/rank/industry-cap grid contains a validation-positive ready-strategy parameter point.",
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": {**design, "parameter_grid": parameter_grid()},
        "sample": design["windows"],
        "execution": design["execution"],
        "gates": design["gates"],
        "falsification_checks": [
            "grid_frozen_before_run",
            "recent_not_used_for_selection",
            "plateau_and_cost_monotonicity",
            "moving_block_bootstrap_from_real_equity_curves",
            "selected_ready_strategy_gbm_100_paths",
            "formal_evidence_passed",
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
        raise RuntimeError("R558 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R558 input changed: {name}")


def _run_window(
    p5: Any,
    service: StrategyBacktestService,
    runtime: dict[str, Any],
    panel: Any,
    *,
    params: dict[str, Any],
    start: date,
    end: date,
    cost_label: str,
    label: str,
    prepared_cache: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    r557 = _r557()
    key = (str(start), str(end))
    prepared = prepared_cache.get(key)
    if prepared is None:
        seed = r557._config(p5, runtime, start=start, end=end, cost_label="2x")
        prepared_base = service.prepare_matrix_optimization([seed])
        prepared = p5._extend_prepared_with_panel(prepared_base, panel, [r557.FACTOR_ID])
        prepared_cache[key] = prepared
    config = r557._config(p5, runtime, start=start, end=end, cost_label=cost_label)
    config.params = {**config.params, **params}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R558:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R558:{label}")
    if result.error:
        raise RuntimeError(f"R558:{label}: {result.error}")
    trades = [trade if isinstance(trade, dict) else service._trade_to_dict(trade) for trade in result.trades]
    return {
        "label": label,
        "variant": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def _plateau_flags(points: list[dict[str, Any]]) -> None:
    by_key = {
        (row["params"]["entry_score"], row["params"]["top_rank"], row["params"]["max_industry_positions"]): row
        for row in points
    }
    axes = (
        ("entry_score", ENTRY_SCORES),
        ("top_rank", TOP_RANKS),
        ("max_industry_positions", INDUSTRY_CAPS),
    )
    for row in points:
        annual = float(row["train"]["stats"]["annual_return"])
        neighbours = []
        for axis, values in axes:
            index = values.index(row["params"][axis])
            for neighbour_index in (index - 1, index + 1):
                if 0 <= neighbour_index < len(values):
                    other = dict(row["params"])
                    other[axis] = values[neighbour_index]
                    neighbour = by_key.get((other["entry_score"], other["top_rank"], other["max_industry_positions"]))
                    if neighbour is not None:
                        neighbours.append(neighbour)
        row["plateau_ok"] = all(
            float(neighbour["train"]["stats"]["annual_return"]) >= max(0.0, annual * 0.70)
            for neighbour in neighbours
        )


def _synthetic_service() -> StrategyBacktestService:
    return StrategyEngine(
        strategy_dirs=[ROOT / "backend" / "app" / "strategy" / "research", DATA / "strategies" / "ai"]
    ), None


def run_policy_path(
    spec: gbm.SyntheticPathSpec,
    *,
    scores: np.ndarray,
    params: dict[str, Any],
    score_name: str,
    asset_count: int = gbm.ASSET_COUNT,
    warmup_days: int = gbm.WARMUP_DAYS,
    evaluation_days: int = gbm.EVALUATION_DAYS,
    symbols: tuple[str, ...] | None = None,
    industries: dict[str, str] | None = None,
    dates: tuple[date, ...] | None = None,
    service: StrategyBacktestService | None = None,
    cost_multipliers: tuple[int, ...] = COSTS,
) -> dict[str, Any]:
    r557 = _r557()
    market = gbm.generate_synthetic_market(spec, asset_count=asset_count, warmup_days=warmup_days, evaluation_days=evaluation_days, symbols=symbols, industries=industries, dates=dates)
    market, resolved_name = gbm.with_fixed_score(market, scores=scores, score_name=score_name)
    base = gbm.build_project_market(market)
    if service is None:
        engine, _ = _synthetic_service()
        service = StrategyBacktestService(BacktestEngine(None), engine)
    params = {**params, "industry_by_symbol": market.industries}
    benchmark_engine = BacktestEngine(None)
    benchmark_market = gbm.build_benchmark_market(market, base_market=base)
    years = evaluation_days / gbm.TRADING_DAYS_PER_YEAR
    benchmarks = {
        cost: gbm._simulation_metrics(benchmark_engine, benchmark_market, gbm.matcher_config(cost, stop_loss_pct=None, max_positions=asset_count, max_hold_days=None), years=years)
        for cost in cost_multipliers
    }
    observations = []

    def make_config(cost: int) -> StrategyBacktestConfig:
        cost_params = gbm.cost_parameters(cost)
        return StrategyBacktestConfig(
            strategy_id=r557.STRATEGY_ID,
            symbols=list(market.symbols),
            start=market.dates[0],
            end=market.dates[-1],
            params=params,
            overrides={"max_hold_days": FIXED["max_hold_days"], "stop_loss": FIXED["stop_loss"], "trailing_stop": None, "basic_filter": r557._base()._synthetic_filter()},
            matching="open_t+1",
            fees_pct=cost_params["fees_pct"],
            commission_pct=cost_params["commission_pct"],
            stamp_tax_pct=cost_params["stamp_tax_pct"],
            slippage_bps=cost_params["slippage_bps"],
            max_positions=FIXED["max_positions"],
            max_exposure_pct=1.0,
            initial_capital=1_000_000.0,
            position_sizing="policy",
            position_policy=r557.POLICY_ID,
            position_policy_params={},
            mode="position",
            asset_type="stock",
            holding_days=FIXED["max_hold_days"],
            hs300=False,
        )

    prepared = service.prepare_matrix_optimization([make_config(cost_multipliers[0])], market_data_override=base)
    for cost in cost_multipliers:
        config = make_config(cost)
        if prepared.signature != service._matrix_prepare_signature(config):
            raise RuntimeError("R558 prepared signature mismatch")
        result = service.run(config, prepared=prepared)
        if result.error:
            raise RuntimeError(f"R558 policy path failed: {result.error}")
        stats = dict(result.stats)
        benchmark = benchmarks[cost]
        observations.append({
            "scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id, "cost_multiplier": cost,
            **{f"strategy_{key}": value for key, value in stats.items() if key in {"total_return", "annual_return", "sharpe", "max_drawdown", "avg_exposure", "max_exposure", "n_trades"}},
            **{f"benchmark_{key}": value for key, value in benchmark.items()},
            "excess_return": float(stats.get("total_return", 0.0)) - float(benchmark["total_return"]),
            "policy": r557.POLICY_ID,
        })
    return {
        "signal_name": resolved_name,
        "policy": r557.POLICY_ID,
        "path_key": {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id},
        "realized": {"close_sha256": bytes_sha256(market.close.tobytes()), "equal_weight_annual_return": market.realized_equal_weight_return, "equal_weight_annual_volatility": market.realized_equal_weight_volatility},
        "benchmarks": [{"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id, "cost_multiplier": cost, **benchmarks[cost]} for cost in cost_multipliers],
        "observations": observations,
    }


def _run_gbm(root: Path, params: dict[str, Any]) -> dict[str, Any]:
    r557 = _r557()
    base = r557._base()
    matrix, symbols, dates, industries = base._score_inputs(root)
    score_industries = {symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}") for index, symbol in enumerate(symbols)}
    engine, _ = _synthetic_service()
    service = StrategyBacktestService(BacktestEngine(None), engine)
    grouped: dict[int, list[dict[str, Any]]] = {path_id: [] for path_id in range(GBM_PATH_COUNT)}
    for path_id in range(GBM_PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in GBM_BETAS:
                result = run_policy_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id), scores=matrix, params=params, score_name=r557.FACTOR_ID,
                    asset_count=300, warmup_days=gbm.WARMUP_DAYS, evaluation_days=GBM_EVALUATION_DAYS,
                    symbols=symbols, industries=score_industries, dates=dates, service=service,
                )
                grouped[path_id].extend(result["observations"])
    paths = [{"path_id": path_id, "observations": rows} for path_id, rows in grouped.items()]
    replay_a = run_policy_path(gbm.SyntheticPathSpec("neutral", 0.0, 0), scores=matrix, params=params, score_name=r557.FACTOR_ID, asset_count=300, warmup_days=gbm.WARMUP_DAYS, evaluation_days=GBM_EVALUATION_DAYS, symbols=symbols, industries=score_industries, dates=dates, service=service)
    replay_b = run_policy_path(gbm.SyntheticPathSpec("neutral", 0.0, 0), scores=matrix, params=params, score_name=r557.FACTOR_ID, asset_count=300, warmup_days=gbm.WARMUP_DAYS, evaluation_days=GBM_EVALUATION_DAYS, symbols=symbols, industries=score_industries, dates=dates, service=service)
    deterministic = replay_a["realized"]["close_sha256"] == replay_b["realized"]["close_sha256"] and replay_a["observations"] == replay_b["observations"]
    summary = summarize_gbm_paths(paths, path_count=GBM_PATH_COUNT, scenarios=set(gbm.SCENARIOS), costs=COSTS, deterministic_replay=deterministic)
    if summary["constraints"]["errors"]:
        raise RuntimeError(f"R558 GBM constraints failed: {summary['constraints']['errors']}")
    return {"summary": summary, "path_observations_sha256": bytes_sha256(json.dumps(paths, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()), "scenario_parameters": {key: {"annual_drift": value.annual_drift, "annual_volatility": value.annual_volatility} for key, value in gbm.SCENARIOS.items()}, "betas": list(GBM_BETAS), "cost_multipliers": list(COSTS), "evaluation_days": GBM_EVALUATION_DAYS}


def _gate(*, full: dict[str, Any], full_3x: dict[str, Any], validation: dict[str, Any], recent: dict[str, Any], fresh: list[dict[str, Any]], long_phase: list[dict[str, Any]]) -> dict[str, Any]:
    fresh_returns = [float(run["stats"].get("total_return") or 0.0) for run in fresh]
    phase_returns = [float(run["stats"].get("total_return") or 0.0) for run in long_phase]
    checks = {
        "full_annual_min": float(full["stats"].get("annual_return") or 0.0) >= 0.10,
        "full_sharpe_min": float(full["stats"].get("sharpe") or 0.0) >= 0.90,
        "full_mdd_min": float(full["stats"].get("max_drawdown") or 0.0) >= -0.18,
        "validation_annual_positive": float(validation["stats"].get("annual_return") or 0.0) > 0.0,
        "stress_3x_annual_min": float(full_3x["stats"].get("annual_return") or 0.0) >= 0.08,
        "recent_return_positive": float(recent["stats"].get("total_return") or 0.0) > 0.0,
        "fresh_positive_min": sum(value > 0.0 for value in fresh_returns) >= 3,
        "fresh_median_positive": bool(fresh_returns) and float(np.median(fresh_returns)) > 0.0,
        "long_phase_positive_min": sum(value > 0.0 for value in phase_returns) >= 6,
        "long_phase_median_positive": bool(phase_returns) and float(np.median(phase_returns)) > 0.0,
    }
    return {"pass": all(checks.values()), "checks": checks, "failed": [name for name, value in checks.items() if not value], "fresh_positive_count": sum(value > 0.0 for value in fresh_returns), "fresh_median": round(float(np.median(fresh_returns)), 8) if fresh_returns else None, "long_phase_positive_count": sum(value > 0.0 for value in phase_returns), "long_phase_median": round(float(np.median(phase_returns)), 8) if phase_returns else None, "five_x_hard_rejection": False}


def _render_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    lines = [
        "# R558 可直接回测策略参数 Monte Carlo",
        "",
        "> 训练选参、验证确认、近期只披露；固定 10 仓、60 日、-12% 止损、14% 波动目标。",
        "",
        "## 选中参数",
        "",
        f"- entry_score：{selected['params']['entry_score']}",
        f"- top_rank：{selected['params']['top_rank']}",
        f"- max_industry_positions：{selected['params']['max_industry_positions']}",
        f"- 训练年化：{selected['train']['stats']['annual_return']:.2%}",
        f"- 验证年化：{selected['validation']['stats']['annual_return']:.2%}",
        "",
        "## 选中结果",
        "",
        f"- 全周期 2x 年化：{payload['full']['2x']['stats']['annual_return']:.2%}",
        f"- 全周期 2x Sharpe：{payload['full']['2x']['stats']['sharpe']:.2f}",
        f"- 全周期 2x MDD：{payload['full']['2x']['stats']['max_drawdown']:.2%}",
        f"- 近期 2x 收益：{payload['recent']['2x']['stats']['total_return']:.2%}",
        f"- 3x 全周期年化：{payload['full']['3x']['stats']['annual_return']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in payload["gate"]["checks"].items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in payload["known_limits"]] + [""]
    return "\n".join(lines)


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    store.load_runtime_registry(DATA)
    r557 = _r557()
    base = _r554()
    panel, runtime = r557._ready_panel(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    hs300 = base._load_hs300_returns(service)
    _, _, candidate_panel = base._score_panel(root)
    equal_weight = base._load_equal_weight_returns(candidate_panel)
    prepared_cache: dict[tuple[str, str], Any] = {}
    raw_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    public_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def run(params: dict[str, Any], start: date, end: date, cost: str) -> dict[str, Any]:
        key = (params["parameter"] if "parameter" in params else json.dumps(params, sort_keys=True), str(start), str(end), cost)
        if key not in public_cache:
            raw = _run_window(p5, service, runtime, panel, params=params, start=start, end=end, cost_label=cost, label=key[0], prepared_cache=prepared_cache)
            raw_cache[key] = raw
            public_cache[key] = base._public_run(raw, base._period_comparison(raw, hs300, equal_weight))
        return public_cache[key]

    points = []
    for point in parameter_grid():
        train = run(point["params"], base.START, base.TRAIN_END, "2x")
        train_3x = run(point["params"], base.START, base.TRAIN_END, "3x")
        validation = run(point["params"], base.VALIDATION_START, base.VALIDATION_END, "2x")
        points.append({"parameter": point["parameter"], "params": point["params"], "train": train, "train_3x": train_3x, "validation": validation, "cost_monotonic": float(train["stats"]["annual_return"]) >= float(train_3x["stats"]["annual_return"])})
    _plateau_flags(points)
    constraints = {"min_train_annual": 0.08, "max_mdd": -0.20, "min_trades": 20, "require_validation_positive": True, "require_plateau": True, "require_cost_monotonic": True, "center": dict(CENTER)}
    provisional = select_standard_parameter(points, constraints)
    if provisional["selected"] is None:
        raise RuntimeError("R558 no ready-strategy parameter point passed train/validation constraints")
    selected_params = provisional["selected"]["params"]
    run(selected_params, base.RECENT_START, base.END, "2x")
    bootstrap_windows = {}
    for name, (start, end) in (("train", (base.START, base.TRAIN_END)), ("validation", (base.VALIDATION_START, base.VALIDATION_END)), ("recent", (base.RECENT_START, base.END))):
        key = (selected_params["parameter"] if "parameter" in selected_params else json.dumps(selected_params, sort_keys=True), str(start), str(end), "2x")
        if key not in raw_cache:
            run(selected_params, start, end, "2x")
        bootstrap_windows[name] = bootstrap_equity_curve(raw_cache[key]["curve"], block_size=BOOTSTRAP_BLOCK, bootstrap_paths=BOOTSTRAP_PATHS, seed=55801 + len(bootstrap_windows))
    bootstrap = {"method": "moving_block_bootstrap", "block_size": BOOTSTRAP_BLOCK, "bootstrap_paths": BOOTSTRAP_PATHS, "windows": bootstrap_windows}
    selection = select_standard_parameter(points, constraints, bootstrap=bootstrap)
    if selection["selected"] is None:
        raise RuntimeError("R558 final selection failed after bootstrap attachment")
    selection["constraints"]["validation_confirmed"] = True
    selected_params = selection["selected"]["params"]
    full = {cost: run(selected_params, base.START, base.END, cost) for cost in ("2x", "3x", "5x")}
    recent = {cost: run(selected_params, base.RECENT_START, base.END, cost) for cost in ("2x", "3x", "5x")}
    long_phase = [run(selected_params, start, base.END, "2x") for start in base.LONG_PHASE_STARTS]
    fresh = [run(selected_params, start, base.END, "2x") for start in base.FRESH_STARTS]
    gate = _gate(full=full["2x"], full_3x=full["3x"], validation=selection["selected"]["validation"], recent=recent["2x"], fresh=fresh, long_phase=long_phase)
    gbm_result = _run_gbm(root, selected_params)
    with tempfile.TemporaryDirectory(prefix="r558_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(gbm_result["summary"]["summary"], plot_dir / "r558_ready_parameter_gbm_distribution.png")
        constraints_plot = plot_gbm_constraints(gbm_result["summary"]["cost_summary"], plot_dir / "r558_ready_parameter_gbm_constraints.png")
        parameter_artifacts = build_standard_parameter_artifacts(selection, plot_dir, prefix="r558_ready_parameter")
        artifacts = [{"kind": "gbm_distribution", "path": distribution.name, "sha256": sha256(distribution)}, {"kind": "gbm_constraints", "path": constraints_plot.name, "sha256": sha256(constraints_plot)}, *parameter_artifacts]
        receipt = build_standard_validation_receipt(gbm_result["summary"], {"status": "complete", "selection_policy": "train_select_validation_confirm", "recent_used_for_selection": False, "grid_size": len(points), "selected": selection["selected"], "constraints": selection["constraints"], "bootstrap": bootstrap, "artifacts": [item for item in parameter_artifacts if item["kind"].startswith("parameter_")]}, artifacts=artifacts)
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R558 standard validation failed: {checked['errors']}")
        conclusion = "watch_only" if gate["pass"] else "rejected"
        payload = {"research_id": RESEARCH_ID, "research_type": RESEARCH_TYPE, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "design": scientific_design(), "selection": selection, "points": points, "full": full, "recent": recent, "long_phase_starts": long_phase, "fresh_starts": fresh, "gate": gate, "bootstrap": bootstrap, "gbm": gbm_result, "standard_validation": receipt, "conclusion": conclusion, "known_limits": ["R558 is a post-R557 parameter search and is not an independent sample.", "The ready strategy's rank factor had negative standalone validation IC in R556B.", "Recent starts are disclosed only and are excluded from selection.", "GBM is a risk-mechanics null and cannot prove real financial alpha.", "No active version, orders, paper targets or production enablement are produced."], "negative_knowledge": [] if gate["pass"] else gate["failed"], "output_policy": scientific_design()["output_policy"]}
        attachments = [(distribution.name, distribution.read_bytes()), (constraints_plot.name, constraints_plot.read_bytes()), *[(item["path"], (plot_dir / item["path"]).read_bytes()) for item in parameter_artifacts]]
        envelope = ResearchResultEnvelope.model_validate({"schema_version": 1, "research_id": RESEARCH_ID, "research_type": RESEARCH_TYPE, "runner": RUNNER, "runner_sha256": sha256(Path(__file__).resolve()), "preregistration_sha256": verify_sidecar(PREREG), "generated_at": payload["generated_at"], "conclusion": payload["conclusion"], "gate_results": gate, "metrics": {"design": payload["design"], "selection": selection, "points": points, "full": full, "recent": recent, "long_phase_starts": long_phase, "fresh_starts": fresh, "gate": gate, "bootstrap": bootstrap, "gbm": gbm_result, "standard_validation": receipt}, "source_result_hashes": [sha256(path) for path in _input_paths(root).values()], "formal_evidence": full["2x"]["formal_evidence"], "known_limits": payload["known_limits"], "negative_knowledge": payload["negative_knowledge"], "output_policy": payload["output_policy"]})
        published = publish_research_artifacts(DATA, preregistration_path=PREREG, result=envelope, markdown=_render_markdown(payload), operation="r558_ready_parameter_mc_published", attachments=attachments).__dict__
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
        raise FileExistsError("R558 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({"conclusion": payload["conclusion"], "selected": payload["selection"]["selected"], "gate": payload["gate"], "full": payload["full"]["2x"]["stats"], "validation": payload["validation"] if "validation" in payload else payload["selection"]["selected"]["validation"]["stats"], "recent": payload["recent"]["2x"]["stats"], "published": payload["published"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
