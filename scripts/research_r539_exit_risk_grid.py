#!/usr/bin/env python3
"""R539: training-only stop/trailing grid for the R538 expanded portfolio."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.factor_research.evidence_guard import require_formal_evidence
from app.research.gate_policy_v2 import evaluate_gate_policy_v2

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r539_exit_risk_grid_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R538C_RESULT = RESEARCH / "r538_expanded_portfolio_execution_grid_result.json"
R538C_PREREG = RESEARCH / "r538_expanded_portfolio_execution_grid_preregistration.json"
GATE_POLICY = (
    DATA
    / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/gate_policy_v2.json"
)
PREREG = RESEARCH / "r539_exit_risk_grid_preregistration.json"
RESULT = RESEARCH / "r539_exit_risk_grid_result.json"
MARKDOWN = RESEARCH / "r539_exit_risk_grid_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 539
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    side = path.with_name(path.name + ".sha256")
    if not side.is_file():
        raise RuntimeError(f"missing sidecar: {path}")
    parts = side.read_text(encoding="ascii").strip().split()
    if len(parts) != 2 or parts[1] != path.name or parts[0] != sha256(path):
        raise RuntimeError(f"invalid sidecar: {path}")
    return parts[0]


def _load_script_module(name: str) -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def exit_grid() -> list[dict[str, Any]]:
    return [
        {"variant_id": "stop_05", "order": 0, "stop_loss": -0.05, "trailing_stop": None},
        {"variant_id": "stop_08", "order": 1, "stop_loss": -0.08, "trailing_stop": None},
        {"variant_id": "stop_12", "order": 2, "stop_loss": -0.12, "trailing_stop": None},
        {"variant_id": "stop_15", "order": 3, "stop_loss": -0.15, "trailing_stop": None},
        {"variant_id": "no_stop", "order": 4, "stop_loss": None, "trailing_stop": None},
        {"variant_id": "stop_08_trail_10", "order": 5, "stop_loss": -0.08, "trailing_stop": -0.10},
        {"variant_id": "stop_12_trail_10", "order": 6, "stop_loss": -0.12, "trailing_stop": -0.10},
    ]


def training_eligible(row: dict[str, Any]) -> bool:
    stats = row.get("stats") or {}
    bootstrap = row.get("bootstrap") or {}
    return (
        isinstance(stats.get("annual_return"), (int, float))
        and stats["annual_return"] >= 0.03
        and isinstance(stats.get("sharpe"), (int, float))
        and stats["sharpe"] >= 0.80
        and isinstance(stats.get("max_drawdown"), (int, float))
        and stats["max_drawdown"] >= -0.20
        and int(row.get("trade_count") or 0) >= 20
        and isinstance(bootstrap.get("total_p05"), (int, float))
        and bootstrap["total_p05"] > 0
    )


def select_exit_variant(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    eligible = [row for row in rows if training_eligible(row)]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -float(row["bootstrap"]["total_p05"]),
            -float(row["stats"]["sharpe"]),
            -float(row["stats"]["annual_return"]),
            int(row["order"]),
        ),
    )


def load_source(root: Path) -> dict[str, Any]:
    root = Path(root)
    verify_sidecar(root / R538C_RESULT.relative_to(ROOT))
    verify_sidecar(root / R538C_PREREG.relative_to(ROOT))
    source = json.loads((root / R538C_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    if source.get("research_type") != "project_formal":
        raise RuntimeError("R538C source is not project_formal")
    if source.get("conclusion") != "rejected":
        raise RuntimeError("R538C source conclusion changed")
    if source["metrics"]["selected"]["max_positions"] != MAX_POSITIONS:
        raise RuntimeError("R538C max positions changed")
    if source["metrics"]["selected"]["max_hold_days"] != MAX_HOLD_DAYS:
        raise RuntimeError("R538C hold changed")
    if source["metrics"]["selected"]["industry_cap"] != INDUSTRY_CAP:
        raise RuntimeError("R538C industry cap changed")
    return {"r538c": source, "source_hash": sha256(root / R538C_RESULT.relative_to(ROOT))}


def load_runtime(root: Path) -> dict[str, Any]:
    r538 = _load_script_module("research_r538_expanded_portfolio_execution_grid")
    runtime = r538.load_runtime(root)
    runtime["r538"] = r538
    return runtime


def prepare_exit_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    variant: dict[str, Any],
    *,
    start: date,
    end: date,
) -> Any:
    seed = p5.portfolio._backtest_config(
        {"score": 1.0},
        {"score": "high"},
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=MAX_HOLD_DAYS,
    )
    seed.symbols = list(runtime["symbols"])
    seed.hs300 = False
    seed.overrides = {
        **(seed.overrides or {}),
        "stop_loss": variant["stop_loss"],
        "trailing_stop": variant["trailing_stop"],
    }
    prepared = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared,
        runtime["panel"],
        ["score", "core_score"],
    )


def run_exit_window(
    p5: Any,
    service: Any,
    prepared: Any,
    runtime: dict[str, Any],
    variant: dict[str, Any],
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
    score_field: str = "score",
) -> dict[str, Any]:
    r533 = runtime["r533"]
    config = r533.build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        max_hold_days=MAX_HOLD_DAYS,
        cost_label=cost_label,
        score_field=score_field,
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": variant["stop_loss"],
        "trailing_stop": variant["trailing_stop"],
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": INDUSTRY_CAP,
    }
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R539:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R539:{label}")
    if result.error:
        raise RuntimeError(f"R539:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "variant_id": variant["variant_id"],
        "order": variant["order"],
        "stop_loss": variant["stop_loss"],
        "trailing_stop": variant["trailing_stop"],
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "score_field": score_field,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": r533._evidence_summary(evidence),
    }


def run_training_grid(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
) -> list[dict[str, Any]]:
    r533 = runtime["r533"]
    rows = []
    for variant in exit_grid():
        prepared = prepare_exit_window(
            p5, service, runtime, variant, start=TRAIN_START, end=TRAIN_END
        )
        run = run_exit_window(
            p5,
            service,
            prepared,
            runtime,
            variant,
            label=f"train_{variant['variant_id']}_2x",
            start=TRAIN_START,
            end=TRAIN_END,
            cost_label="2x",
        )
        run["bootstrap"] = r533.bootstrap_curve(
            run["curve"],
            seed=BOOTSTRAP_SEED * 100 + variant["order"],
        )
        run["eligible"] = training_eligible(run)
        rows.append(run)
    return rows


def run_confirmation(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    r533 = runtime["r533"]
    selected_variant = next(
        row for row in exit_grid() if row["variant_id"] == selected["variant_id"]
    )
    default_variant = next(row for row in exit_grid() if row["variant_id"] == "stop_08")
    full_prepared = prepare_exit_window(
        p5, service, runtime, selected_variant, start=TRAIN_START, end=END
    )
    full = {
        cost: run_exit_window(
            p5,
            service,
            full_prepared,
            runtime,
            selected_variant,
            label=f"full_{selected['variant_id']}_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_full = run_exit_window(
        p5,
        service,
        prepare_exit_window(
            p5, service, runtime, default_variant, start=TRAIN_START, end=END
        ),
        runtime,
        default_variant,
        label="control_full_stop_08_2x",
        start=TRAIN_START,
        end=END,
        cost_label="2x",
    )
    validation_prepared = prepare_exit_window(
        p5, service, runtime, selected_variant, start=VALIDATION_START, end=VALIDATION_END
    )
    validation = run_exit_window(
        p5,
        service,
        validation_prepared,
        runtime,
        selected_variant,
        label="validation_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    recent_prepared = prepare_exit_window(
        p5, service, runtime, selected_variant, start=RECENT_START, end=END
    )
    recent = {
        cost: run_exit_window(
            p5,
            service,
            recent_prepared,
            runtime,
            selected_variant,
            label=f"recent_{selected['variant_id']}_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_recent = run_exit_window(
        p5,
        service,
        prepare_exit_window(
            p5, service, runtime, default_variant, start=RECENT_START, end=END
        ),
        runtime,
        default_variant,
        label="control_recent_stop_08_2x",
        start=RECENT_START,
        end=END,
        cost_label="2x",
    )
    full["2x"]["bootstrap"] = r533.bootstrap_curve(full["2x"]["curve"], seed=53901)
    recent["2x"]["bootstrap"] = r533.bootstrap_curve(recent["2x"]["curve"], seed=53902)
    fresh = []
    for start in FRESH_STARTS:
        prepared = prepare_exit_window(
            p5, service, runtime, selected_variant, start=start, end=END
        )
        run = run_exit_window(
            p5,
            service,
            prepared,
            runtime,
            selected_variant,
            label=f"fresh_{start.isoformat()}_2x",
            start=start,
            end=END,
            cost_label="2x",
        )
        fresh.append({
            "start": start.isoformat(),
            "total_return": run["stats"]["total_return"],
            "annual_return": run["stats"]["annual_return"],
            "sharpe": run["stats"]["sharpe"],
            "max_drawdown": run["stats"]["max_drawdown"],
            "formal_evidence": run["formal_evidence"],
        })
    return {
        "full": full,
        "validation": validation,
        "recent": recent,
        "control_full": control_full,
        "control_recent": control_recent,
        "fresh_starts": fresh,
        "yearly": r533.yearly_returns(full["2x"]["curve"]),
    }


def build_gate_payload(
    runtime: dict[str, Any],
    training: list[dict[str, Any]],
    confirmation: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    r533 = runtime["r533"]
    full_2x = confirmation["full"]["2x"]
    full_3x = confirmation["full"]["3x"]
    full_5x = confirmation["full"]["5x"]
    recent_2x = confirmation["recent"]["2x"]
    recent_3x = confirmation["recent"]["3x"]
    recent_5x = confirmation["recent"]["5x"]
    control_full = confirmation["control_full"]
    control_recent = confirmation["control_recent"]
    concentration = r533.concentration_metrics(
        full_2x["trades"], runtime["industries"], max_positions=MAX_POSITIONS
    )
    concentration["net_to_gross"] = r533._net_to_gross(full_2x["trades"])
    all_runs = [
        *training,
        full_2x,
        full_3x,
        full_5x,
        recent_2x,
        recent_3x,
        recent_5x,
        confirmation["validation"],
        control_full,
        control_recent,
    ]
    evidence_ok = r533.formal_evidence_pass(all_runs)
    checks = {
        "input_sidecars_valid": True,
        "r526_daily_membership": True,
        "formal_evidence_saved_lookahead_t1": evidence_ok,
        "industry_cap_applied": INDUSTRY_CAP > 0,
        "stop_variant_declared": selected["stop_loss"] is not None,
        "no_leverage": True,
        "open_t_plus_one": True,
        "limit_and_suspension_constraints": True,
    }
    main_2x = {
        **full_2x["stats"],
        "annual": full_2x["stats"]["annual_return"],
        "mdd": full_2x["stats"]["max_drawdown"],
        "recent": recent_2x["stats"]["total_return"],
        "year_2022": confirmation["yearly"].get("2022"),
        "year_2023": confirmation["yearly"].get("2023"),
        "fresh_starts": [row["total_return"] for row in confirmation["fresh_starts"]],
    }
    stress = {
        **full_3x["stats"],
        "annual": full_3x["stats"]["annual_return"],
        "mdd": full_3x["stats"]["max_drawdown"],
        "recent": recent_3x["stats"]["total_return"],
        "cost_factor": 3,
    }
    extreme = {
        **full_5x["stats"],
        "annual": full_5x["stats"]["annual_return"],
        "mdd": full_5x["stats"]["max_drawdown"],
        "recent": recent_5x["stats"]["total_return"],
        "cost_factor": 5,
        "net_to_gross": concentration["net_to_gross"],
    }
    candidate = {
        "id": "r539_exit_grid",
        "max_positions": MAX_POSITIONS,
        "is_multi_asset": False,
        "data_integrity": {"pass": all(checks.values()), "checks": checks},
        "no_leverage": True,
        "main_2x": main_2x,
        "control_2x": {
            "annual": control_full["stats"]["annual_return"],
            "sharpe": control_full["stats"]["sharpe"],
            "recent": control_recent["stats"]["total_return"],
        },
        "stress_3x": stress,
        "extreme_5x": extreme,
        "concentration": concentration,
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    evaluation = evaluate_gate_policy_v2(candidate, policy_path=GATE_POLICY)
    validation_positive = confirmation["validation"]["stats"]["annual_return"] > 0
    recent_excess = (
        recent_2x["stats"]["total_return"]
        - control_recent["stats"]["total_return"]
    )
    recent_improved = recent_excess > 0
    hard_pass = (
        evaluation["status"] == "qualified_for_paper_review"
        and validation_positive
        and recent_improved
    )
    failed = r533._failed_leaf_paths(evaluation["gates"])
    if not validation_positive:
        failed.append("validation_annual_positive")
    if not recent_improved:
        failed.append("recent_excess_vs_stop_08")
    return {
        "candidate": candidate,
        "evaluation": evaluation,
        "validation_positive": validation_positive,
        "recent_excess_vs_stop_08": round(recent_excess, 6),
        "recent_improved": recent_improved,
        "conclusion": "watch_only" if hard_pass else "rejected",
        "failed_gate_paths": failed,
        "formal_evidence": full_2x["formal_evidence"],
    }


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"curve", "trades"}}


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = load_source(root)
    runtime = load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    training = run_training_grid(p5, service, runtime)
    selected = select_exit_variant(training)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "research_id": "R539",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r538c": R538C_RESULT.relative_to(ROOT).as_posix(),
        "source_hash": source["source_hash"],
        "execution_controls": {
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "industry_cap": INDUSTRY_CAP,
        },
        "training_grid": [_public_run(row) for row in training],
        "exit_grid": exit_grid(),
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    if selected is None:
        payload.update({
            "selected": None,
            "selection_status": "no_exit_variant",
            "confirmation": None,
            "gate": {
                "conclusion": "rejected",
                "failed_gate_paths": ["training_exit_variant"],
                "evaluation": None,
            },
            "conclusion": "rejected",
            "negative_knowledge": ["training_exit_variant"],
            "formal_evidence": training[0]["formal_evidence"],
            "known_limits": [
                "No stop/trailing variant passed the training-only eligibility gates.",
                "No validation or recent return was used to select the exit variant.",
                "No leverage, orders, paper targets or active version are produced.",
            ],
        })
        return payload, runtime
    confirmation = run_confirmation(p5, service, runtime, selected)
    gate = build_gate_payload(runtime, training, confirmation, selected)
    payload.update({
        "selected": _public_run(selected),
        "selection_status": "selected_on_training_only",
        "confirmation": {
            "full": {cost: _public_run(row) for cost, row in confirmation["full"].items()},
            "validation": _public_run(confirmation["validation"]),
            "recent": {cost: _public_run(row) for cost, row in confirmation["recent"].items()},
            "control_full": _public_run(confirmation["control_full"]),
            "control_recent": _public_run(confirmation["control_recent"]),
            "fresh_starts": confirmation["fresh_starts"],
            "yearly": confirmation["yearly"],
        },
        "gate": {
            **gate,
            "failed_gate_paths": gate["failed_gate_paths"],
        },
        "conclusion": gate["conclusion"],
        "formal_evidence": gate["formal_evidence"],
        "negative_knowledge": gate["failed_gate_paths"],
        "known_limits": [
            "The 13 factor members, 10 positions, 60-day hold and industry cap are frozen from R538C.",
            "Exit parameters are selected only on the training window.",
            "Recent-year results have been observed repeatedly and are not an independent discovery sample.",
            "The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
    })
    return payload, runtime


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R539 扩展组合退出风控网格",
        "",
        "## 训练网格",
        "",
        "| 变体 | stop | trailing | 年化 | Sharpe | MDD | p05 | 交易数 | 资格 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["training_grid"]:
        s = row["stats"]
        b = row["bootstrap"]
        lines.append(
            f"| {row['variant_id']} | {row['stop_loss']} | {row['trailing_stop']} | "
            f"{s['annual_return']} | {s['sharpe']} | {s['max_drawdown']} | "
            f"{b['total_p05']} | {row['trade_count']} | {row['eligible']} |"
        )
    lines += ["", "## 选中变体", ""]
    selected = payload.get("selected")
    if selected is None:
        lines.append("- 没有退出变体通过训练资格。")
    else:
        lines += [
            f"- 变体：`{selected['variant_id']}`",
            f"- stop：{selected['stop_loss']}",
            f"- trailing：{selected['trailing_stop']}",
            f"- 训练年化：{selected['stats']['annual_return']}",
            f"- 训练 Sharpe：{selected['stats']['sharpe']}",
            f"- bootstrap p05：{selected['bootstrap']['total_p05']}",
        ]
    confirmation = payload.get("confirmation")
    if confirmation:
        lines += [
            "",
            "## 确认结果",
            "",
            "| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for label, row in (
            ("full 2x", confirmation["full"]["2x"]),
            ("full 3x", confirmation["full"]["3x"]),
            ("full 5x", confirmation["full"]["5x"]),
            ("validation 2x", confirmation["validation"]),
            ("recent 2x", confirmation["recent"]["2x"]),
            ("recent 3x", confirmation["recent"]["3x"]),
            ("recent 5x", confirmation["recent"]["5x"]),
            ("control full 2x", confirmation["control_full"]),
            ("control recent 2x", confirmation["control_recent"]),
        ):
            s = row["stats"]
            lines.append(
                f"| {label} | {s['annual_return']} | {s['sharpe']} | "
                f"{s['max_drawdown']} | {s['total_return']} | {row['trade_count']} |"
            )
        lines += ["", "## Fresh starts", ""]
        lines += [f"- {row['start']}: {row['total_return']}" for row in confirmation["fresh_starts"]]
    lines += [
        "",
        "## 门禁",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 失败路径：{payload['gate']['failed_gate_paths']}",
    ]
    evaluation = payload["gate"].get("evaluation")
    if evaluation is not None:
        lines += [
            f"- v2 状态：{evaluation['status']}",
            f"- 5× warnings：{evaluation['diagnostics']['5x_warnings']}",
            f"- 5× 硬否决：{evaluation['diagnostics']['5x_hard_rejection']}",
        ]
    lines += ["", "## 边界", ""]
    lines += [f"- {item}" for item in payload["known_limits"]]
    if payload["negative_knowledge"]:
        lines += ["", "## 负知识", ""]
        lines += [f"- {item}" for item in payload["negative_knowledge"]]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R539 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R539 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R539 preregistration already exists: {PREREG}")
    root = Path(root)
    source = load_source(root)
    payload = {
        "schema_version": 1,
        "research_id": "R539",
        "research_type": RESEARCH_TYPE,
        "question": "Can a training-only stop/trailing-stop grid turn the R538C recent-year loss positive without breaking the full-period gate?",
        "hypothesis": "A tighter or trailing exit reduces recent drawdown while preserving full-period risk-adjusted return.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {
            "r538c_result": source["source_hash"],
            "r538c_preregistration": sha256(root / R538C_PREREG.relative_to(ROOT)),
            "gate_policy_v2": sha256(root / GATE_POLICY.relative_to(ROOT)),
            "r526_membership": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/daily_snapshots.parquet"),
            "r526_industry": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/industry_history.parquet"),
        },
        "parameters": {
            "exit_grid": exit_grid(),
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "industry_cap": INDUSTRY_CAP,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "bootstrap_block": BOOTSTRAP_BLOCK,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "selection_order": ["bootstrap_p05_desc", "sharpe_desc", "annual_desc", "order_asc"],
        },
        "sample": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(day) for day in FRESH_STARTS],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "training_annual_min": 0.03,
            "training_sharpe_min": 0.80,
            "training_mdd_min": -0.20,
            "training_trades_min": 20,
            "training_bootstrap_p05_positive": True,
            "validation_annual_positive": True,
            "recent_excess_vs_stop_08": True,
            "gate_policy_v2": GATE_POLICY.relative_to(ROOT).as_posix(),
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "training_only_exit_selection",
            "fixed_13_factor_execution",
            "industry_cap_applied",
            "formal_evidence_saved_lookahead_t1",
            "five_x_warning_only",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    PREREG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    PREREG.with_name(PREREG.name + ".sha256").write_text(
        f"{sha256(PREREG)}  {PREREG.name}\n", encoding="ascii"
    )
    return PREREG


def publish_result(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    root = Path(root)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError(f"R539 result already exists: {RESULT}")
    prereg_hash = verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    result = ResearchResultEnvelope.model_validate({
        "schema_version": 1,
        "research_id": prereg["research_id"],
        "research_type": RESEARCH_TYPE,
        "runner": prereg["runner"],
        "runner_sha256": prereg["runner_sha256"],
        "preregistration_sha256": prereg_hash,
        "generated_at": payload["generated_at"],
        "conclusion": payload["conclusion"],
        "gate_results": payload["gate"],
        "metrics": {
            "source_r538c": payload["source_r538c"],
            "execution_controls": payload["execution_controls"],
            "training_grid": payload["training_grid"],
            "selected": payload["selected"],
            "selection_status": payload["selection_status"],
            "confirmation": payload["confirmation"],
        },
        "source_result_hashes": [
            payload["source_hash"],
        ],
        "formal_evidence": payload["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r539_exit_risk_grid_published",
    ).__dict__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--freeze-preregistration", action="store_true")
    args = parser.parse_args()
    if args.freeze_preregistration:
        path = freeze_preregistration(ROOT)
        print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix()}))
        return 0
    if args.preflight:
        print(json.dumps({
            "exit_grid": exit_grid(),
            "execution_controls": {
                "max_positions": MAX_POSITIONS,
                "max_hold_days": MAX_HOLD_DAYS,
                "industry_cap": INDUSTRY_CAP,
            },
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R539 results already exist; refusing overwrite")
    payload, _runtime = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "selection_status": payload["selection_status"],
        "selected": payload["selected"],
        "failed_gate_paths": payload["gate"]["failed_gate_paths"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
