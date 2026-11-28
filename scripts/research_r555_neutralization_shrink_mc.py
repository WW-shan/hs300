#!/usr/bin/env python3
"""R555: train/validation Monte Carlo for industry-neutralization shrink."""

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

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

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R555B"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r555b_neutralization_shrink_mc_preregistration.json"
)
RUNNER = "scripts/research_r555_neutralization_shrink_mc.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
PREREG = RESEARCH / "r555b_neutralization_shrink_mc_preregistration.json"
RESULT = RESEARCH / "r555b_neutralization_shrink_mc_result.json"
MARKDOWN = RESEARCH / "r555b_neutralization_shrink_mc_result.md"
R554_RESULT = RESEARCH / "r554_industry_neutral_confirmation_result.json"
R555_ATTEMPT_PREREG = RESEARCH / "r555_neutralization_shrink_mc_preregistration.json"
R555_ATTEMPT_FAILURE = RESEARCH / "r555_attempt1_public_run_contract_failure.json"
SHRINKS = (0.0, 0.25, 0.50, 0.75, 1.0)
CENTER_SHRINK = 0.50
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_PATHS = 2000
GBM_SEEDS = (55501, 55502, 55503)


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


def neutralization(shrink: float) -> dict[str, Any] | None:
    if not math.isfinite(shrink) or not 0.0 <= shrink <= 1.0:
        raise ValueError("shrink must be in [0, 1]")
    if shrink == 0.0:
        return None
    return {"industry": "industry_l1", "styles": [], "shrink": shrink}


def parameter_grid() -> list[dict[str, Any]]:
    return [
        {
            "parameter": f"shrink_{round(shrink * 100):02d}",
            "params": {"shrink": shrink},
            "neutralize": neutralization(shrink),
        }
        for shrink in SHRINKS
    ]


def scientific_design() -> dict[str, Any]:
    base = _base()
    return {
        "question": (
            "Which fixed industry-neutralization shrink maximizes robust training and "
            "validation performance without using the recent window for selection?"
        ),
        "research_lineage": ["R552C", "R554"],
        "fixed_parameters": dict(base.FIXED_PARAMETERS),
        "grid": {"shrink": list(SHRINKS)},
        "grid_size": len(SHRINKS),
        "center": {"shrink": CENTER_SHRINK},
        "selection": {
            "policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "ranking": "maximize min(train_annual, validation_annual) after hard constraints",
            "post_r554_observation": True,
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
        "windows": base.scientific_design()["windows"],
        "standard_validation": {
            "suite_version": "v1",
            "gbm": {"required": True, "path_count": 100},
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
    inputs = _base()._input_paths(root)
    inputs["r554_result"] = root / R554_RESULT.relative_to(ROOT)
    inputs["r555_attempt_preregistration"] = root / R555_ATTEMPT_PREREG.relative_to(ROOT)
    inputs["r555_attempt_failure"] = root / R555_ATTEMPT_FAILURE.relative_to(ROOT)
    return inputs


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R555 preregistration already exists: {PREREG}")
    design = scientific_design()
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": design["question"],
        "hypothesis": (
            "A partial industry-neutralization shrink may retain R552C's full-period return "
            "while improving validation and recent drawdown robustness."
        ),
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
            "shrink_zero_is_raw_control",
            "train_2x_vs_3x_cost_monotonicity",
            "parameter_plateau",
            "moving_block_bootstrap_from_real_equity_curves",
            "selected_variant_full_gbm_100_paths",
            "project_000300_sh_and_equal_weight_benchmarks",
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
        raise RuntimeError("R555 runner hash no longer matches preregistration")
    for name, path in _input_paths(root).items():
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R555 input changed: {name}")


def _run_window(
    root: Path,
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: Any,
    *,
    shrink: float,
    start: date,
    end: date,
    cost_label: str,
    label: str,
    prepared_cache: dict[tuple[str, str], Any],
) -> dict[str, Any]:
    base = _base()
    r545 = _load_module("research_r545_stable7_risk_budget")
    r533 = runtime["r533"]
    prepare_key = (str(start), str(end))
    prepared = prepared_cache.get(prepare_key)
    if prepared is None:
        prepared = r545.prepare_panel(
            p5,
            service,
            runtime,
            panel,
            start=start,
            end=end,
        )
        prepared_cache[prepare_key] = prepared
    config = r533.build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=base.FIXED_PARAMETERS["max_positions"],
        max_hold_days=base.FIXED_PARAMETERS["max_hold_days"],
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": base.FIXED_PARAMETERS["stop_loss"],
        "trailing_stop": None,
    }
    neutralize = neutralization(shrink)
    if neutralize is not None:
        config.overrides = {**config.overrides, "neutralize": neutralize}
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": base.FIXED_PARAMETERS["industry_cap"],
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "policy"
    config.position_policy = base._policy_variant()["id"]
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R555:{label}: prepared signature mismatch")
    with r545.policy_injection(base._policy_variant()):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = base.require_formal_evidence(result, label=f"R555:{label}")
        if result.error:
            raise RuntimeError(f"R555:{label}: {result.error}")
        trades = [
            trade if isinstance(trade, dict) else service._trade_to_dict(trade)
            for trade in result.trades
        ]
    return {
        "label": label,
        "variant": f"shrink_{round(shrink * 100):02d}",
        "shrink": shrink,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": r533._evidence_summary(evidence),
    }


def _plateau_flags(points: list[dict[str, Any]]) -> None:
    by_shrink = {float(row["params"]["shrink"]): row for row in points}
    for row in points:
        shrink = float(row["params"]["shrink"])
        index = SHRINKS.index(shrink)
        neighbours = [
            by_shrink[SHRINKS[neighbour]]
            for neighbour in (index - 1, index + 1)
            if 0 <= neighbour < len(SHRINKS)
        ]
        annual = float(row["train"]["stats"]["annual_return"])
        row["plateau_ok"] = all(
            float(neighbour["train"]["stats"]["annual_return"]) >= max(0.0, annual * 0.70)
            for neighbour in neighbours
        )


def _gate(
    *,
    full: dict[str, Any],
    full_3x: dict[str, Any],
    validation: dict[str, Any],
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


def _run_gbm(root: Path, shrink: float) -> dict[str, Any]:
    base = _base()
    gbm = base.gbm
    matrix, symbols, dates, industries = base._score_inputs(root)
    score_industries = {
        symbol: industries.get(symbol, f"SYN_IND_{index // 10:02d}")
        for index, symbol in enumerate(symbols)
    }
    service = base._synthetic_service()
    grouped: dict[int, list[dict[str, Any]]] = {
        path_id: [] for path_id in range(base.GBM_PATH_COUNT)
    }
    for path_id in range(base.GBM_PATH_COUNT):
        for scenario_id in gbm.SCENARIOS:
            for beta in base.GBM_BETAS:
                result = base.run_policy_path(
                    gbm.SyntheticPathSpec(scenario_id, beta, path_id),
                    scores=matrix,
                    neutralize=neutralization(shrink),
                    asset_count=300,
                    warmup_days=gbm.WARMUP_DAYS,
                    evaluation_days=base.GBM_EVALUATION_DAYS,
                    max_positions=base.FIXED_PARAMETERS["max_positions"],
                    max_hold_days=base.FIXED_PARAMETERS["max_hold_days"],
                    top_rank=20,
                    industry_cap=base.FIXED_PARAMETERS["industry_cap"],
                    symbols=symbols,
                    industries=score_industries,
                    dates=dates,
                    service=service,
                )
                grouped[path_id].extend(result["observations"])
    paths = [{"path_id": path_id, "observations": rows} for path_id, rows in grouped.items()]
    replay_a = base.run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix,
        neutralize=neutralization(shrink),
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=base.GBM_EVALUATION_DAYS,
        max_positions=base.FIXED_PARAMETERS["max_positions"],
        max_hold_days=base.FIXED_PARAMETERS["max_hold_days"],
        top_rank=20,
        industry_cap=base.FIXED_PARAMETERS["industry_cap"],
        symbols=symbols,
        industries=score_industries,
        dates=dates,
        service=service,
    )
    replay_b = base.run_policy_path(
        gbm.SyntheticPathSpec("neutral", 0.0, 0),
        scores=matrix,
        neutralize=neutralization(shrink),
        asset_count=300,
        warmup_days=gbm.WARMUP_DAYS,
        evaluation_days=base.GBM_EVALUATION_DAYS,
        max_positions=base.FIXED_PARAMETERS["max_positions"],
        max_hold_days=base.FIXED_PARAMETERS["max_hold_days"],
        top_rank=20,
        industry_cap=base.FIXED_PARAMETERS["industry_cap"],
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
        path_count=base.GBM_PATH_COUNT,
        scenarios=set(gbm.SCENARIOS),
        costs=base.GBM_COSTS,
        deterministic_replay=deterministic,
    )
    if summary["constraints"]["errors"]:
        raise RuntimeError(f"R555 GBM constraints failed: {summary['constraints']['errors']}")
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
        "betas": list(base.GBM_BETAS),
        "cost_multipliers": list(base.GBM_COSTS),
        "evaluation_days": base.GBM_EVALUATION_DAYS,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    selected = payload["selection"]["selected"]
    lines = [
        "# R555 行业中性 shrink 参数 Monte Carlo",
        "",
        "> 训练选参、验证确认、近期只披露；R554 已观察全周期和近期，因此本轮最多 `watch_only`。",
        "",
        "## 选中参数",
        "",
        f"- shrink：{selected['params']['shrink']:.2f}",
        f"- 训练年化：{selected['train']['stats']['annual_return']:.2%}",
        f"- 验证年化：{selected['validation']['stats']['annual_return']:.2%}",
        "",
        "## 参数点",
        "",
        "| shrink | 训练年化 | 验证年化 | 训练MDD | 平台 | 成本单调 |",
        "| ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in payload["points"]:
        lines.append(
            f"| {row['params']['shrink']:.2f} | {row['train']['stats']['annual_return']:.2%} | "
            f"{row['validation']['stats']['annual_return']:.2%} | "
            f"{row['train']['stats']['max_drawdown']:.2%} | "
            f"{'是' if row['plateau_ok'] else '否'} | "
            f"{'是' if row['cost_monotonic'] else '否'} |"
        )
    lines += [
        "",
        "## 选中结果",
        "",
        f"- 全周期 2x 年化：{payload['full']['2x']['stats']['annual_return']:.2%}",
        f"- 全周期 2x Sharpe：{payload['full']['2x']['stats']['sharpe']:.2f}",
        f"- 全周期 2x MDD：{payload['full']['2x']['stats']['max_drawdown']:.2%}",
        f"- 近期 2x 收益：{payload['recent']['2x']['stats']['total_return']:.2%}",
        f"- 3x 全周期年化：{payload['full']['3x']['stats']['annual_return']:.2%}",
        f"- 5x 近期年化：{payload['recent']['5x']['stats']['annual_return']:.2%}（仅警告）",
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
    base = _base()
    panel, runtime, candidate_panel = base._score_panel(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    hs300 = base._load_hs300_returns(service)
    equal_weight = base._load_equal_weight_returns(candidate_panel)
    prepared_cache: dict[tuple[str, str], Any] = {}
    raw_cache: dict[tuple[float, str, str, str], dict[str, Any]] = {}
    public_cache: dict[tuple[float, str, str, str], dict[str, Any]] = {}

    def run(shrink: float, start: date, end: date, cost: str) -> dict[str, Any]:
        key = (shrink, str(start), str(end), cost)
        if key not in public_cache:
            raw = _run_window(
                root,
                p5,
                service,
                runtime,
                panel,
                shrink=shrink,
                start=start,
                end=end,
                cost_label=cost,
                label=f"shrink_{shrink:.2f}_{start}_{end}_{cost}",
                prepared_cache=prepared_cache,
            )
            raw_cache[key] = raw
            public_cache[key] = base._public_run(
                raw,
                base._period_comparison(raw, hs300, equal_weight),
            )
        return public_cache[key]

    points: list[dict[str, Any]] = []
    for shrink in SHRINKS:
        train = run(shrink, base.START, base.TRAIN_END, "2x")
        train_3x = run(shrink, base.START, base.TRAIN_END, "3x")
        validation = run(shrink, base.VALIDATION_START, base.VALIDATION_END, "2x")
        points.append({
            "parameter": f"shrink_{round(shrink * 100):02d}",
            "params": {"shrink": shrink},
            "train": train,
            "train_3x": train_3x,
            "validation": validation,
            "cost_monotonic": float(train["stats"]["annual_return"])
            >= float(train_3x["stats"]["annual_return"]),
        })
    _plateau_flags(points)
    constraints = {
        "min_train_annual": 0.08,
        "max_mdd": -0.20,
        "min_trades": 20,
        "require_validation_positive": True,
        "require_plateau": True,
        "require_cost_monotonic": True,
        "center": {"shrink": CENTER_SHRINK},
    }
    provisional = select_standard_parameter(points, constraints)
    if provisional["selected"] is None:
        raise RuntimeError("R555 no shrink passed train/validation constraints")
    selected_shrink = float(provisional["selected"]["params"]["shrink"])
    run(selected_shrink, base.RECENT_START, base.END, "2x")
    bootstrap_windows = {}
    for name, key in (
        ("train", (selected_shrink, str(base.START), str(base.TRAIN_END), "2x")),
        ("validation", (selected_shrink, str(base.VALIDATION_START), str(base.VALIDATION_END), "2x")),
        ("recent", (selected_shrink, str(base.RECENT_START), str(base.END), "2x")),
    ):
        bootstrap_windows[name] = bootstrap_equity_curve(
            raw_cache[key]["curve"],
            block_size=BOOTSTRAP_BLOCK,
            bootstrap_paths=BOOTSTRAP_PATHS,
            seed=55501 + len(bootstrap_windows),
        )
    bootstrap = {
        "method": "moving_block_bootstrap",
        "block_size": BOOTSTRAP_BLOCK,
        "bootstrap_paths": BOOTSTRAP_PATHS,
        "windows": bootstrap_windows,
    }
    selection = select_standard_parameter(points, constraints, bootstrap=bootstrap)
    if selection["selected"] is None:
        raise RuntimeError("R555 final selection failed after bootstrap attachment")
    selection["constraints"]["validation_confirmed"] = True
    selected_shrink = float(selection["selected"]["params"]["shrink"])

    full = {cost: run(selected_shrink, base.START, base.END, cost) for cost in ("2x", "3x", "5x")}
    recent = {cost: run(selected_shrink, base.RECENT_START, base.END, cost) for cost in ("2x", "3x", "5x")}
    long_phase = [
        run(selected_shrink, start, base.END, "2x")
        for start in base.LONG_PHASE_STARTS
    ]
    fresh = [
        run(selected_shrink, start, base.END, "2x")
        for start in base.FRESH_STARTS
    ]
    gate = _gate(
        full=full["2x"],
        full_3x=full["3x"],
        validation=selection["selected"]["validation"],
        recent=recent["2x"],
        fresh=fresh,
        long_phase=long_phase,
    )

    gbm_result = _run_gbm(root, selected_shrink)
    with tempfile.TemporaryDirectory(prefix="r555_plots_") as raw_dir:
        plot_dir = Path(raw_dir)
        distribution = plot_gbm_distribution(
            gbm_result["summary"]["summary"],
            plot_dir / "r555_neutralization_shrink_gbm_distribution.png",
        )
        constraints_plot = plot_gbm_constraints(
            gbm_result["summary"]["cost_summary"],
            plot_dir / "r555_neutralization_shrink_gbm_constraints.png",
        )
        parameter_artifacts = build_standard_parameter_artifacts(
            selection,
            plot_dir,
            prefix="r555_neutralization_shrink",
        )
        artifacts = [
            {"kind": "gbm_distribution", "path": distribution.name, "sha256": sha256(distribution)},
            {"kind": "gbm_constraints", "path": constraints_plot.name, "sha256": sha256(constraints_plot)},
            *parameter_artifacts,
        ]
        parameter_receipt = {
            "status": "complete",
            "selection_policy": "train_select_validation_confirm",
            "recent_used_for_selection": False,
            "grid_size": len(SHRINKS),
            "selected": selection["selected"],
            "constraints": selection["constraints"],
            "bootstrap": bootstrap,
            "artifacts": [
                item for item in parameter_artifacts
                if item["kind"].startswith("parameter_")
            ],
        }
        receipt = build_standard_validation_receipt(
            gbm_result["summary"],
            parameter_receipt,
            artifacts=artifacts,
        )
        checked = check_standard_validation_constraints(receipt, research_type=RESEARCH_TYPE)
        if not checked["pass"]:
            raise RuntimeError(f"R555 standard validation failed: {checked['errors']}")
        conclusion = "watch_only" if gate["pass"] else "rejected"
        payload = {
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "design": scientific_design(),
            "selection": selection,
            "points": points,
            "full": full,
            "recent": recent,
            "long_phase_starts": long_phase,
            "fresh_starts": fresh,
            "gate": gate,
            "bootstrap": bootstrap,
            "gbm": gbm_result,
            "standard_validation": receipt,
            "conclusion": conclusion,
            "known_limits": [
                "R555 was designed after R554 observations, so recent/full results are not independent replications.",
                "Recent starts are disclosed only and are excluded from parameter ranking.",
                "The selected variant remains watch-only with active_version=null and no orders.",
                "GBM is a risk-mechanics null and cannot prove financial alpha.",
                "5x cost is warning-only; 2x is the baseline and 3x is the stress gate.",
            ],
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
        prereg_hash = verify_sidecar(PREREG)
        envelope = ResearchResultEnvelope.model_validate({
            "schema_version": 1,
            "research_id": RESEARCH_ID,
            "research_type": RESEARCH_TYPE,
            "runner": RUNNER,
            "runner_sha256": sha256(Path(__file__).resolve()),
            "preregistration_sha256": prereg_hash,
            "generated_at": payload["generated_at"],
            "conclusion": payload["conclusion"],
            "gate_results": {**gate, "selection": selection["constraints"]},
            "metrics": {
                "design": payload["design"],
                "selection": selection,
                "points": points,
                "full": full,
                "recent": recent,
                "long_phase_starts": long_phase,
                "fresh_starts": fresh,
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
        published = publish_research_artifacts(
            DATA,
            preregistration_path=PREREG,
            result=envelope,
            markdown=_render_markdown(payload),
            operation="r555b_neutralization_shrink_mc_published",
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
        raise FileExistsError("R555 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selected": payload["selection"]["selected"],
        "gate": payload["gate"],
        "full": payload["full"]["2x"]["stats"],
        "recent": payload["recent"]["2x"]["stats"],
        "published": payload["published"],
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
