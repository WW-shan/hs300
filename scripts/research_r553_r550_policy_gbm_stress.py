#!/usr/bin/env python3
"""R553: GBM path stress for R550 with the causal vol_12 position policy."""

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
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)
from app.factor_research.standard_validation import (  # noqa: E402
    build_standard_validation_receipt,
    check_standard_validation_constraints,
    plot_gbm_constraints,
    plot_gbm_distribution,
    summarize_gbm_paths,
)
from app.strategy.engine import StrategyEngine  # noqa: E402

RESEARCH_TYPE = "synthetic_market_stress"
RESEARCH_ID = "R553"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r553_r550_policy_gbm_stress_preregistration.json"
)
RUNNER = "scripts/research_r553_r550_policy_gbm_stress.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R550_RESULT = RESEARCH / "r550_cash_safety_confirmation_result.json"
R551_RESULT = RESEARCH / "r551a_r550_gbm_stress_result.json"
R552_RESULT = RESEARCH / "r552c_cash_safety_parameter_mc_result.json"
PREREG = RESEARCH / "r553_r550_policy_gbm_stress_preregistration.json"
RESULT = RESEARCH / "r553_r550_policy_gbm_stress_result.json"
MARKDOWN = RESEARCH / "r553_r550_policy_gbm_stress_result.md"
START = date(2024, 1, 2)
EVALUATION_DAYS = 504
PATH_COUNT = 100
BETA_VALUES = (0.0, 0.7)
COSTS = (2, 3, 5)
POLICY = "vol_12"
POLICY_VARIANT = {
    "id": POLICY,
    "kind": "vol_target",
    "target": 0.12,
    "lookback": 60,
    "floor": 0.50,
    "order": 12,
}
FIXED_ADDONS = ("cf_r547_cash_quality", "cf_r547_value_quality_safety")


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
    score_name: str = "r550_cash_safety",
    asset_count: int = gbm.ASSET_COUNT,
    warmup_days: int = gbm.WARMUP_DAYS,
    evaluation_days: int = gbm.EVALUATION_DAYS,
    max_positions: int = gbm.MAX_POSITIONS,
    max_hold_days: int = gbm.MAX_HOLD_DAYS,
    top_rank: int = 20,
    industry_cap: int = gbm.INDUSTRY_CAP,
    symbols: tuple[str, ...] | None = None,
    industries: dict[str, str] | None = None,
    dates: tuple[date, ...] | None = None,
    service: StrategyBacktestService | None = None,
    cost_multipliers: tuple[int, ...] = COSTS,
) -> dict[str, Any]:
    """Run one synthetic path with the frozen score and project policy chain."""
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
    strategy_engine = service.strategy_engine
    strategy = strategy_engine.get("factor_rank_research")
    if strategy.execution_backend != "matrix_native":
        raise RuntimeError("R553 requires the factor_rank_research matrix strategy")
    # Synthetic bars are intentionally outside the production board and amount
    # filters; the strategy logic and matcher, not the real-market filter, are
    # being stress-tested here.
    filter_override = _synthetic_filter()
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
        return StrategyBacktestConfig(
            strategy_id="factor_rank_research",
            symbols=list(market.symbols),
            start=market.dates[0],
            end=market.dates[-1],
            params=params,
            overrides={
                "max_hold_days": max_hold_days,
                "stop_loss": -0.12,
                "trailing_stop": None,
                "basic_filter": filter_override,
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
            position_policy=POLICY,
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
            raise RuntimeError("R553 prepared signature mismatch")
        with r545.policy_injection(POLICY_VARIANT):
            result = service.run(config, prepared=prepared)
        if result.error:
            raise RuntimeError(f"R553 policy path failed: {result.error}")
        stats = dict(result.stats)
        benchmark = benchmarks[cost]
        observations.append({
            "scenario_id": spec.scenario_id,
            "beta": spec.beta,
            "path_id": spec.path_id,
            "cost_multiplier": cost,
            **{f"strategy_{key}": value for key, value in stats.items()
               if key in {"total_return", "annual_return", "sharpe", "max_drawdown", "avg_exposure", "max_exposure", "n_trades"}},
            **{f"benchmark_{key}": value for key, value in benchmark.items()},
            "excess_return": float(stats.get("total_return", 0.0)) - float(benchmark["total_return"]),
            "policy": POLICY,
        })
    return {
        "signal_name": resolved_name,
        "policy": POLICY,
        "path_key": {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id},
        "realized": {
            "close_sha256": hashlib.sha256(market.close.tobytes()).hexdigest(),
            "equal_weight_annual_return": market.realized_equal_weight_return,
            "equal_weight_annual_volatility": market.realized_equal_weight_volatility,
        },
        "benchmarks": [
            {"scenario_id": spec.scenario_id, "beta": spec.beta, "path_id": spec.path_id, "cost_multiplier": cost, **benchmarks[cost]}
            for cost in cost_multipliers
        ],
        "observations": observations,
    }


def scientific_design() -> dict[str, Any]:
    return {
        "model": "geometric_brownian_motion",
        "path_count": PATH_COUNT,
        "evaluation_days": EVALUATION_DAYS,
        "scenarios": [
            {"scenario_id": key, "annual_drift": value.annual_drift, "annual_volatility": value.annual_volatility}
            for key, value in gbm.SCENARIOS.items()
        ],
        "betas": list(BETA_VALUES),
        "cost_multipliers": list(COSTS),
        "policy": POLICY,
        "policy_variant": POLICY_VARIANT,
        "fixed_addons": list(FIXED_ADDONS),
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": PATH_COUNT},
            "parameter_mc": {
                "required": False,
                "status": "fixed_frozen",
                "reason": "R552C owns parameter selection; R553 applies the fixed vol_12 policy to GBM",
            },
        },
        "execution": {
            "matching": "open_t+1",
            "max_exposure_pct": 1.0,
            "leverage": "none",
            "five_x_policy": "warning_only",
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
        "r550_result": root / R550_RESULT.relative_to(ROOT),
        "r551_result": root / R551_RESULT.relative_to(ROOT),
        "r552_result": root / R552_RESULT.relative_to(ROOT),
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R553 preregistration already exists: {PREREG}")
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "How does the frozen R550 cash_safety strategy with the causal vol_12 "
            "position policy perform under the standardized GBM path matrix?"
        ),
        "hypothesis": (
            "The policy will enforce the 12% volatility budget and preserve the "
            "no-leverage constraint under synthetic paths, but GBM remains a "
            "diagnostic null for real financial alpha."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "synthetic_gbm",
        "synthetic_model": {
            "model": "geometric_brownian_motion",
            "parameters": {
                "scenarios": scientific_design()["scenarios"],
                "betas": list(BETA_VALUES),
                "cost_multipliers": list(COSTS),
            },
            "seed": gbm.DEFAULT_MASTER_SEED,
            "path_count": PATH_COUNT,
            "benchmark": "same_path_equal_weight_buy_and_hold",
        },
        "input_hashes": {name: sha256(path) for name, path in _input_paths(root).items()},
        "parameters": scientific_design(),
        "sample": {
            "start": str(START),
            "evaluation_days": EVALUATION_DAYS,
            "path_count": PATH_COUNT,
            "asset_count": 300,
            "scenario_count": len(gbm.SCENARIOS),
            "beta_count": len(BETA_VALUES),
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "path_count": PATH_COUNT,
            "scenarios": len(gbm.SCENARIOS),
            "cost_multipliers": list(COSTS),
            "deterministic_replay": True,
            "no_leverage": True,
            "same_path_benchmark": True,
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "policy_injection_through_project_service",
            "vol_12_budget_scale_le_1",
            "same_seed_byte_identical_replay",
            "signal_t_fills_at_t_plus_one_open_only",
            "same_path_equal_weight_benchmark",
            "plugins_and_artifacts_sidecar_bound",
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
        raise RuntimeError("R553 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R553 input changed: {name}")


def _score_inputs(root: Path) -> tuple[np.ndarray, tuple[str, ...], tuple[date, ...], dict[str, str]]:
    r551 = _load_module("research_r551_r550_gbm_stress")
    panel, industries = r551._score_panel(root)
    dates = r551._target_dates(panel)
    symbols = r551._target_symbols(panel, dates)
    matrix, symbols, dates = r551.build_frozen_score_matrix(panel, target_dates=dates, target_symbols=symbols)
    return matrix, symbols, dates, industries


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    matrix, symbols, dates, industries = _score_inputs(root)
    score_industries = {
        symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}")
        for index, symbol in enumerate(symbols)
    }
    service = _synthetic_service()
    grouped: dict[int, list[dict[str, Any]]] = {path_id: [] for path_id in range(PATH_COUNT)}
    for path_id in range(PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in BETA_VALUES:
                result = run_policy_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                    scores=matrix,
                    asset_count=300,
                    warmup_days=gbm.WARMUP_DAYS,
                    evaluation_days=EVALUATION_DAYS,
                    max_positions=10,
                    max_hold_days=60,
                    top_rank=20,
                    industry_cap=3,
                    symbols=symbols,
                    industries=score_industries,
                    dates=dates,
                    service=service,
                )
                grouped[path_id].extend(result["observations"])
    paths = [{"path_id": path_id, "observations": rows} for path_id, rows in grouped.items()]
    replay_a = run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix, asset_count=300, warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=EVALUATION_DAYS, max_positions=10, max_hold_days=60,
        top_rank=20, industry_cap=3, symbols=symbols, industries=score_industries,
        dates=dates, service=service,
    )
    replay_b = run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix, asset_count=300, warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=EVALUATION_DAYS, max_positions=10, max_hold_days=60,
        top_rank=20, industry_cap=3, symbols=symbols, industries=score_industries,
        dates=dates, service=service,
    )
    deterministic = replay_a["realized"]["close_sha256"] == replay_b["realized"]["close_sha256"] and replay_a["observations"] == replay_b["observations"]
    gbm_summary = summarize_gbm_paths(
        paths, path_count=PATH_COUNT, scenarios=set(gbm.SCENARIOS), costs=COSTS,
        deterministic_replay=deterministic,
    )
    if gbm_summary["constraints"]["errors"]:
        raise RuntimeError(f"R553 GBM constraints failed: {gbm_summary['constraints']['errors']}")
    with tempfile.TemporaryDirectory(prefix="r553_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(gbm_summary["summary"], plot_dir / "r553_r550_policy_gbm_distribution.png")
        constraints_plot = plot_gbm_constraints(gbm_summary["cost_summary"], plot_dir / "r553_r550_policy_gbm_constraints.png")
        artifacts = [
            {"kind": "gbm_distribution", "path": distribution.name, "sha256": sha256(distribution)},
            {"kind": "gbm_constraints", "path": constraints_plot.name, "sha256": sha256(constraints_plot)},
        ]
        receipt = build_standard_validation_receipt(
            gbm_summary,
            {"status": "fixed_frozen", "reason": "R553 uses the frozen R550 vol_12 policy", "recent_used_for_selection": False},
            artifacts=artifacts,
        )
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R553 standard validation failed: {checked['errors']}")
        payload = {
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "design": scientific_design(),
            "gbm": gbm_summary,
            "paths": paths,
            "standard_validation": receipt,
            "conclusion": "diagnostic_only",
            "known_limits": [
                "This is a synthetic GBM stress test, not real HS300 alpha evidence.",
                "The score matrix is frozen from R550; synthetic prices intentionally contain no financial-factor signal.",
                "The vol_12 policy is applied through the project StrategyBacktestService and causal PositionSizingContext.",
                "5x cost is warning-only and synthetic results cannot admit, order or enable a strategy.",
            ],
            "negative_knowledge": [],
            "output_policy": scientific_design()["output_policy"],
        }
        published = _publish(payload, [(distribution.name, distribution.read_bytes()), (constraints_plot.name, constraints_plot.read_bytes())])
    return {**payload, "published": published}


def _publish(payload: dict[str, Any], attachments: list[tuple[str, bytes]]) -> dict[str, Any]:
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "runner": RUNNER,
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["gbm"]["constraints"],
        "metrics": {
            "design": payload["design"],
            "gbm": payload["gbm"],
            "paths": payload["paths"],
            "standard_validation": payload["standard_validation"],
        },
        "source_result_hashes": [sha256(R550_RESULT), sha256(R551_RESULT), sha256(R552_RESULT)],
        "formal_evidence": None,
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA, preregistration_path=PREREG, result=result,
        markdown=_render_markdown(payload), operation="r553_r550_policy_gbm_stress_published",
        attachments=attachments,
    ).__dict__


def _render_markdown(payload: dict[str, Any]) -> str:
    gbm_summary = payload["gbm"]
    lines = [
        "# R553 R550 vol_12 策略级 GBM 压力测试",
        "",
        "> `diagnostic_only`：使用真实 R550 分数、合成 GBM 报价和项目 vol_12 仓位策略。",
        "",
        "## 标准轴",
        "",
        f"- 路径：{gbm_summary['path_count']} 条",
        f"- 情景：{gbm_summary['scenarios']} 个",
        f"- 成本：{gbm_summary['cost_multipliers']}",
        f"- 仓位策略：`{POLICY}`",
        "",
        "## 情景结果",
        "",
        "| 情景 | 超额 p05 | 超额 p50 | 正超额比例 | MDD p50 | Sharpe p50 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in gbm_summary["summary"].items():
        lines.append(f"| {name} | {row['excess_p05']:.2%} | {row['excess_p50']:.2%} | {row['positive_excess_ratio']:.2%} | {row['mdd_p50']:.2%} | {row['sharpe_p50']:.2f} |")
    lines += ["", "## 成本结果", "", "| 成本 | 年化 p50 | MDD p50 | 超额 p50 |", "| ---: | ---: | ---: | ---: |"]
    for cost, row in gbm_summary["cost_summary"].items():
        lines.append(f"| {cost}x | {row['annual_return_p50']:.2%} | {row['mdd_p50']:.2%} | {row['excess_p50']:.2%} |")
    lines += ["", "## 回执", "", f"- 约束错误：{gbm_summary['constraints']['errors']}", f"- 图表：{', '.join(item['path'] for item in payload['standard_validation']['artifacts'])}", "", "## 边界", "", *[f"- {item}" for item in payload["known_limits"]], ""]
    return "\n".join(lines)


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
        raise FileExistsError("R553 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({"conclusion": payload["conclusion"], "gbm": payload["gbm"]["summary"], "cost": payload["gbm"]["cost_summary"], "published": payload["published"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
