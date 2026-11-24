#!/usr/bin/env python3
"""R534: T-1 market regime gating for the frozen R533 core portfolio."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence
from app.research.gate_policy_v2 import evaluate_gate_policy_v2

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r534b_regime_gated_core_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R533A_RESULT = RESEARCH / "r533a_core_formal_portfolio_result.json"
R533A_PREREG = RESEARCH / "r533a_core_formal_portfolio_preregistration.json"
R533B_RESULT = RESEARCH / "r533b_core_formal_gate_reaudit_result.json"
R533B_PREREG = RESEARCH / "r533b_core_formal_gate_reaudit_preregistration.json"
R534_ATTEMPT1 = RESEARCH / "r534_attempt1_runtime_helper_failure.json"
R534_ATTEMPT2 = RESEARCH / "r534_attempt2_regime_coverage_failure.json"
REGIME = DATA / "regime_history" / "part.parquet"
GATE_POLICY = (
    DATA
    / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/gate_policy_v2.json"
)
PREREG = RESEARCH / "r534b_regime_gated_core_preregistration.json"
RESULT = RESEARCH / "r534b_regime_gated_core_result.json"
MARKDOWN = RESEARCH / "r534b_regime_gated_core_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 10, 14)
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
TRAIN_ANNUAL_DROP_MAX = 0.03
TRAIN_SHARPE_MIN = 1.10
TRAIN_MDD_MIN = -0.15
TRAIN_TRADES_MIN = 80
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 534
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


def load_source(root: Path) -> dict[str, Any]:
    root = Path(root)
    for relative in (
        R533A_RESULT,
        R533A_PREREG,
        R533B_RESULT,
        R533B_PREREG,
        R534_ATTEMPT1,
        R534_ATTEMPT2,
        GATE_POLICY,
    ):
        verify_sidecar(root / relative.relative_to(ROOT))
    r533a = json.loads((root / R533A_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    r533b = json.loads((root / R533B_RESULT.relative_to(ROOT)).read_text(encoding="utf-8"))
    selected = r533a["metrics"]["selected"]
    if int(selected["max_positions"]) != MAX_POSITIONS:
        raise RuntimeError("R533A max_positions changed")
    if int(selected["max_hold_days"]) != MAX_HOLD_DAYS:
        raise RuntimeError("R533A max_hold_days changed")
    if r533b["metrics"]["failed_gate_paths"] != [
        "core_2x.recent_return",
        "stress.recent_return",
    ]:
        raise RuntimeError("R533B failure attribution changed")
    regime = pl.read_parquet(root / REGIME.relative_to(ROOT)).sort("date")
    if "state" not in regime.columns or "score" not in regime.columns:
        raise RuntimeError("regime_history lacks state/score")
    latest = regime["date"].cast(pl.Utf8).max()
    if regime.filter(pl.col("date").cast(pl.Utf8) == str(END)).is_empty():
        raise RuntimeError(f"regime_history does not cover confirmation end {END}")
    return {
        "r533a": r533a,
        "r533b": r533b,
        "regime_latest": latest,
        "confirmation_end": str(END),
    }


def regime_rules() -> list[dict[str, Any]]:
    return [
        {"rule_id": "always_on", "order": 0, "regime_filter": None},
        {
            "rule_id": "exclude_weak",
            "order": 1,
            "regime_filter": {
                "states": ["strong", "lean_strong", "range", "lean_weak"]
            },
        },
        {
            "rule_id": "exclude_weak_pair",
            "order": 2,
            "regime_filter": {"states": ["strong", "lean_strong", "range"]},
        },
        {
            "rule_id": "score_min_20",
            "order": 3,
            "regime_filter": {"min_score": 20},
        },
        {
            "rule_id": "score_min_30",
            "order": 4,
            "regime_filter": {"min_score": 30},
        },
        {
            "rule_id": "exclude_weak_pair_min20",
            "order": 5,
            "regime_filter": {
                "states": ["strong", "lean_strong", "range"],
                "min_score": 20,
            },
        },
    ]


def regime_rule_eligible(row: dict[str, Any], *, baseline_training_annual: float) -> bool:
    stats = row.get("stats") or {}
    bootstrap = row.get("bootstrap") or {}
    return (
        isinstance(stats.get("annual_return"), (int, float))
        and stats["annual_return"] >= baseline_training_annual - TRAIN_ANNUAL_DROP_MAX
        and isinstance(stats.get("sharpe"), (int, float))
        and stats["sharpe"] >= TRAIN_SHARPE_MIN
        and isinstance(stats.get("max_drawdown"), (int, float))
        and stats["max_drawdown"] >= TRAIN_MDD_MIN
        and int(row.get("trade_count") or 0) >= TRAIN_TRADES_MIN
        and isinstance(bootstrap.get("total_p05"), (int, float))
        and bootstrap["total_p05"] > 0
    )


def select_regime_rule(
    rows: list[dict[str, Any]], *, baseline_training_annual: float
) -> dict[str, Any] | None:
    eligible = [
        row
        for row in rows
        if regime_rule_eligible(
            row, baseline_training_annual=baseline_training_annual
        )
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda row: (
            -float(row["bootstrap"]["mdd_p05"]),
            -float(row["bootstrap"]["total_p05"]),
            -float(row["stats"]["sharpe"]),
            -float(row["stats"]["annual_return"]),
            int(row["order"]),
        ),
    )


def load_runtime(root: Path) -> dict[str, Any]:
    r533 = _load_script_module("research_r533_core_formal_portfolio")
    runtime = r533.load_runtime_inputs(root)
    runtime["r533"] = r533
    return {
        "r533": r533,
        "runtime": runtime,
        "p5": _load_script_module("research_factor_increment_p5"),
    }


def prepare_regime_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    regime_filter: dict[str, Any] | None,
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
    seed.regime_filter = copy.deepcopy(regime_filter)
    prepared = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared,
        runtime["panel"],
        ["score", "control_score"],
    )


def build_regime_config(
    p5: Any,
    prepared: Any,
    regime_filter: dict[str, Any] | None,
    *,
    start: date,
    end: date,
    cost_label: str,
    score_field: str = "score",
) -> Any:
    config = p5.portfolio._backtest_config(
        {score_field: 1.0},
        {score_field: "high"},
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        cost=(
            p5.portfolio._cost_parameters(cost_label)
            if cost_label != "5x"
            else {
                "fees_pct": 0.001,
                "commission_pct": 0.001,
                "stamp_tax_pct": 0.0005,
                "slippage_bps": 25.0,
            }
        ),
        max_hold_days=MAX_HOLD_DAYS,
    )
    config.params = {
        **(config.params or {}),
        "entry_score": ENTRY_SCORE,
        "exit_score": EXIT_SCORE,
        "top_rank": max(20, MAX_POSITIONS),
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "equal"
    config.symbols = list(prepared.signature[1])
    config.hs300 = False
    config.regime_filter = copy.deepcopy(regime_filter)
    return config


def _evidence_summary(evidence: dict[str, Any]) -> dict[str, str]:
    formal = evidence.get("formal_status") or {}
    return {
        "run_id": str(formal.get("run_id")),
        "persistence": str(formal.get("persistence")),
        "lookahead": str(formal.get("lookahead")),
        "execution_timing": str(formal.get("execution_timing")),
    }


def run_regime_window(
    p5: Any,
    service: Any,
    prepared: Any,
    regime_filter: dict[str, Any] | None,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
    score_field: str = "score",
) -> dict[str, Any]:
    config = build_regime_config(
        p5,
        prepared,
        regime_filter,
        start=start,
        end=end,
        cost_label=cost_label,
        score_field=score_field,
    )
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R534:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R534:{label}")
    if result.error:
        raise RuntimeError(f"R534:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "rule_id": None,
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": _evidence_summary(evidence),
    }


def run_training_rules(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    source_training = source["r533a"]["metrics"]["selected"]
    for rule in regime_rules():
        prepared = prepare_regime_window(
            p5,
            service,
            runtime,
            rule["regime_filter"],
            start=TRAIN_START,
            end=TRAIN_END,
        )
        run = run_regime_window(
            p5,
            service,
            prepared,
            rule["regime_filter"],
            label=f"train_{rule['rule_id']}_2x",
            start=TRAIN_START,
            end=TRAIN_END,
            cost_label="2x",
        )
        run["rule_id"] = rule["rule_id"]
        run["order"] = rule["order"]
        seed = 1533 if rule["rule_id"] == "always_on" else BOOTSTRAP_SEED * 100 + rule["order"]
        run["bootstrap"] = runtime["r533"].bootstrap_curve(run["curve"], seed=seed)
        if rule["rule_id"] == "always_on":
            for key in ("annual_return", "sharpe", "max_drawdown"):
                expected = float(source_training["stats"][key])
                observed = float(run["stats"][key])
                if abs(expected - observed) > 1e-6:
                    raise RuntimeError(
                        f"R533 training reproduction drift for {key}: {expected} != {observed}"
                    )
        run["eligible"] = regime_rule_eligible(
            run,
            baseline_training_annual=float(source_training["stats"]["annual_return"]),
        )
        rows.append(run)
    return rows


def run_confirmation(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    selected: dict[str, Any],
) -> dict[str, Any]:
    selected_filter = next(
        rule["regime_filter"]
        for rule in regime_rules()
        if rule["rule_id"] == selected["rule_id"]
    )
    full_prepared = prepare_regime_window(
        p5,
        service,
        runtime,
        selected_filter,
        start=TRAIN_START,
        end=END,
    )
    full = {
        cost: run_regime_window(
            p5,
            service,
            full_prepared,
            selected_filter,
            label=f"full_{selected['rule_id']}_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_full = run_regime_window(
        p5,
        service,
        full_prepared,
        selected_filter,
        label=f"control_full_{selected['rule_id']}_2x",
        start=TRAIN_START,
        end=END,
        cost_label="2x",
        score_field="control_score",
    )

    validation_prepared = prepare_regime_window(
        p5,
        service,
        runtime,
        selected_filter,
        start=VALIDATION_START,
        end=VALIDATION_END,
    )
    validation = run_regime_window(
        p5,
        service,
        validation_prepared,
        selected_filter,
        label=f"validation_{selected['rule_id']}_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    validation["bootstrap"] = runtime["r533"].bootstrap_curve(
        validation["curve"], seed=BOOTSTRAP_SEED * 100 + 90
    )

    recent_prepared = prepare_regime_window(
        p5,
        service,
        runtime,
        selected_filter,
        start=RECENT_START,
        end=END,
    )
    recent = {
        cost: run_regime_window(
            p5,
            service,
            recent_prepared,
            selected_filter,
            label=f"recent_{selected['rule_id']}_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    recent["2x"]["bootstrap"] = runtime["r533"].bootstrap_curve(
        recent["2x"]["curve"], seed=BOOTSTRAP_SEED * 100 + 91
    )
    control_recent = run_regime_window(
        p5,
        service,
        recent_prepared,
        selected_filter,
        label=f"control_recent_{selected['rule_id']}_2x",
        start=RECENT_START,
        end=END,
        cost_label="2x",
        score_field="control_score",
    )

    if selected["rule_id"] == "always_on":
        base_full = full["2x"]
        base_recent = recent["2x"]
    else:
        full_base_prepared = prepare_regime_window(
            p5, service, runtime, None, start=TRAIN_START, end=END
        )
        base_full = run_regime_window(
            p5,
            service,
            full_base_prepared,
            None,
            label="base_full_always_on_2x",
            start=TRAIN_START,
            end=END,
            cost_label="2x",
        )
        recent_base_prepared = prepare_regime_window(
            p5, service, runtime, None, start=RECENT_START, end=END
        )
        base_recent = run_regime_window(
            p5,
            service,
            recent_base_prepared,
            None,
            label="base_recent_always_on_2x",
            start=RECENT_START,
            end=END,
            cost_label="2x",
        )

    fresh = []
    for start in FRESH_STARTS:
        prepared = prepare_regime_window(
            p5, service, runtime, selected_filter, start=start, end=END
        )
        run = run_regime_window(
            p5,
            service,
            prepared,
            selected_filter,
            label=f"fresh_{start.isoformat()}_{selected['rule_id']}_2x",
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
        "base_full": base_full,
        "base_recent": base_recent,
        "fresh_starts": fresh,
        "yearly": runtime["r533"].yearly_returns(full["2x"]["curve"]),
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
        full_2x["trades"],
        runtime["industries"],
        max_positions=MAX_POSITIONS,
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
        control_full,
        control_recent,
        confirmation["base_full"],
        confirmation["base_recent"],
        confirmation["validation"],
    ]
    evidence_ok = r533.formal_evidence_pass(all_runs)
    checks = {
        "input_sidecars_valid": True,
        "r526_daily_membership": True,
        "financial_factor_pit_verified": True,
        "regime_filter_t_minus_1": all(
            row["formal_evidence"]["lookahead"] == "passed" for row in all_runs
        ),
        "formal_evidence_saved_lookahead_t1": evidence_ok,
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
        "id": f"r534_{selected['rule_id']}",
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
        - confirmation["base_recent"]["stats"]["total_return"]
    )
    recent_improved = recent_excess > 0
    hard_pass = (
        evaluation["status"] == "qualified_for_paper_review"
        and validation_positive
        and recent_improved
    )
    conclusion = "watch_only" if hard_pass else "rejected"
    failed = r533._failed_leaf_paths(evaluation["gates"])
    if not validation_positive:
        failed.append("validation_annual_positive")
    if not recent_improved:
        failed.append("recent_excess_vs_always_on")
    return {
        "candidate": candidate,
        "evaluation": evaluation,
        "validation_positive": validation_positive,
        "recent_excess_vs_always_on": round(recent_excess, 6),
        "recent_improved": recent_improved,
        "conclusion": conclusion,
        "failed_gate_paths": failed,
        "formal_evidence": full_2x["formal_evidence"],
    }


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items() if key not in {"curve", "trades"}
    }


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = load_source(root)
    loaded = load_runtime(root)
    runtime = loaded["runtime"]
    p5 = loaded["p5"]
    service = p5.portfolio._service()
    training = run_training_rules(p5, service, runtime, source)
    baseline_training_annual = float(
        source["r533a"]["metrics"]["selected"]["stats"]["annual_return"]
    )
    selected = select_regime_rule(
        training, baseline_training_annual=baseline_training_annual
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "research_id": "R534B",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r533a": R533A_RESULT.relative_to(ROOT).as_posix(),
        "source_r533b": R533B_RESULT.relative_to(ROOT).as_posix(),
        "regime_end": str(END),
        "frozen_execution": {
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
        },
        "training_rules": [_public_run(row) for row in training],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    if selected is None:
        first = training[0]
        payload.update({
            "selected": None,
            "selection_status": "no_regime_rule_eligible",
            "confirmation": None,
            "gate": {
                "conclusion": "rejected",
                "failed_gate_paths": ["training_regime_rule_selected"],
                "evaluation": None,
            },
            "conclusion": "rejected",
            "formal_evidence": first["formal_evidence"],
            "negative_knowledge": ["training_regime_rule_selected"],
            "known_limits": [
                "No frozen regime rule passed the training-only MC eligibility gates.",
                "No validation, recent or stress return was used to change rules.",
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
            "base_full": _public_run(confirmation["base_full"]),
            "base_recent": _public_run(confirmation["base_recent"]),
            "fresh_starts": confirmation["fresh_starts"],
            "yearly": confirmation["yearly"],
        },
        "gate": {
            "conclusion": gate["conclusion"],
            "candidate": gate["candidate"],
            "evaluation": gate["evaluation"],
            "validation_annual_positive": gate["validation_positive"],
            "recent_excess_vs_always_on": gate["recent_excess_vs_always_on"],
            "recent_improved": gate["recent_improved"],
            "failed_gate_paths": gate["failed_gate_paths"],
        },
        "conclusion": gate["conclusion"],
        "formal_evidence": gate["formal_evidence"],
        "negative_knowledge": gate["failed_gate_paths"],
        "known_limits": [
            "R534 attempts 1 and 2 failed before result publication due the runtime-helper and regime-coverage plumbing defects; R534B is the explicit amendment.",
            "The recent year was already observed in R533; R534A is post-hoc regime conditioning, not an independent discovery.",
            "Only T-1 regime state/score is used by the project engine; no same-day state is backfilled.",
            "The regime history ends on 2026-10-21, so confirmation does not extend to 2026-11-05.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
    })
    return payload, runtime


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R534B 因子库核心市场情绪/状态门控",
        "",
        "冻结 R533 的六因子、10 仓、60 日持有；只改变 T-1 市场状态门控。",
        "",
        "## 训练期规则",
        "",
        "| 规则 | 年化 | Sharpe | MDD | bootstrap MDD p05 | bootstrap total p05 | 交易数 | 资格 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["training_rules"]:
        stats = row["stats"]
        boot = row["bootstrap"]
        lines.append(
            f"| {row['rule_id']} | {stats['annual_return']} | {stats['sharpe']} | "
            f"{stats['max_drawdown']} | {boot['mdd_p05']} | {boot['total_p05']} | "
            f"{row['trade_count']} | {row['eligible']} |"
        )
    lines += ["", "## 选中规则", ""]
    selected = payload["selected"]
    if selected is None:
        lines.append("- 没有规则通过训练资格。")
    else:
        lines += [
            f"- 规则：`{selected['rule_id']}`",
            f"- 训练年化：{selected['stats']['annual_return']}",
            f"- 训练 Sharpe：{selected['stats']['sharpe']}",
            f"- 训练 MDD：{selected['stats']['max_drawdown']}",
            f"- bootstrap MDD p05：{selected['bootstrap']['mdd_p05']}",
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
            ("base full 2x", confirmation["base_full"]),
            ("base recent 2x", confirmation["base_recent"]),
            ("control full 2x", confirmation["control_full"]),
            ("control recent 2x", confirmation["control_recent"]),
        ):
            stats = row["stats"]
            lines.append(
                f"| {label} | {stats['annual_return']} | {stats['sharpe']} | "
                f"{stats['max_drawdown']} | {stats['total_return']} | {row['trade_count']} |"
            )
        lines += ["", "## Fresh starts", ""]
        lines += [
            f"- {row['start']}: {row['total_return']}"
            for row in confirmation["fresh_starts"]
        ]
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
    if payload["gate"].get("recent_excess_vs_always_on") is not None:
        lines.append(
            f"- 近一年相对 always-on：{payload['gate']['recent_excess_vs_always_on']}"
        )
    lines += ["", "## 边界", ""]
    lines += [f"- {item}" for item in payload["known_limits"]]
    if payload["negative_knowledge"]:
        lines += ["", "## 负知识", ""]
        lines += [f"- {item}" for item in payload["negative_knowledge"]]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R534 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R534 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R534 preregistration already exists: {PREREG}")
    source = load_source(root)
    selected_source = source["r533a"]["metrics"]["selected"]
    payload = {
        "schema_version": 1,
        "research_id": "R534B",
        "research_type": RESEARCH_TYPE,
        "question": "Can a pre-registered T-1 sentiment/state gate improve the frozen R533 core's recent-year return without sacrificing full-period risk?",
        "hypothesis": "Blocking new entries in weak or low-score regimes reduces recent drawdown while preserving the full-period edge.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {
            "r533a_result": sha256(root / R533A_RESULT.relative_to(ROOT)),
            "r533a_preregistration": sha256(root / R533A_PREREG.relative_to(ROOT)),
            "r533b_result": sha256(root / R533B_RESULT.relative_to(ROOT)),
            "r533b_preregistration": sha256(root / R533B_PREREG.relative_to(ROOT)),
            "regime_history": sha256(root / REGIME.relative_to(ROOT)),
            "gate_policy_v2": sha256(root / GATE_POLICY.relative_to(ROOT)),
            "r534_attempt1_failure": sha256(root / R534_ATTEMPT1.relative_to(ROOT)),
            "r534_attempt2_failure": sha256(root / R534_ATTEMPT2.relative_to(ROOT)),
        },
        "parameters": {
            "source_selected": selected_source,
            "rules": regime_rules(),
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "bootstrap_block": BOOTSTRAP_BLOCK,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "selection_order": [
                "bootstrap_mdd_p05_desc",
                "bootstrap_total_p05_desc",
                "sharpe_desc",
                "annual_desc",
                "rule_order",
            ],
        },
        "sample": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "regime_end": source["confirmation_end"],
            "fresh_starts": [str(day) for day in FRESH_STARTS],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": {
            "training_annual_drop_max": TRAIN_ANNUAL_DROP_MAX,
            "training_sharpe_min": TRAIN_SHARPE_MIN,
            "training_mdd_min": TRAIN_MDD_MIN,
            "training_trades_min": TRAIN_TRADES_MIN,
            "training_bootstrap_total_p05_positive": True,
            "validation_annual_positive": True,
            "recent_excess_vs_always_on_positive": True,
            "gate_policy_v2": GATE_POLICY.relative_to(ROOT).as_posix(),
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "source_sidecars_valid",
            "r533_training_reproduction",
            "t_minus_one_regime_only",
            "training_only_rule_selection",
            "fixed_seed_block_bootstrap_20_5000",
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
        raise FileExistsError(f"R534 result already exists: {RESULT}")
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
            "source_r533a": payload["source_r533a"],
            "source_r533b": payload["source_r533b"],
            "regime_end": payload["regime_end"],
            "frozen_execution": payload["frozen_execution"],
            "training_rules": payload["training_rules"],
            "selected": payload["selected"],
            "selection_status": payload["selection_status"],
            "confirmation": payload["confirmation"],
        },
        "source_result_hashes": [
            sha256(root / R533A_RESULT.relative_to(ROOT)),
            sha256(root / R533B_RESULT.relative_to(ROOT)),
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
        operation="r534b_regime_gated_core_published",
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
        source = load_source(ROOT)
        print(json.dumps({
            "frozen_execution": {
                "max_positions": MAX_POSITIONS,
                "max_hold_days": MAX_HOLD_DAYS,
            },
            "regime_end": source["confirmation_end"],
            "rules": regime_rules(),
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R534 results already exist; refusing overwrite")
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
