#!/usr/bin/env python3
"""R537: formal portfolio test of the 13 independent factor representatives."""

from __future__ import annotations

import argparse
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
    "data/factor_research/research/"
    "r537c_expanded_factor_portfolio_preregistration.json"
)
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R533A_RESULT = RESEARCH / "r533a_core_formal_portfolio_result.json"
R533A_PREREG = RESEARCH / "r533a_core_formal_portfolio_preregistration.json"
R536A_RESULT = RESEARCH / "r536a_finalist_cluster_fdr_result.json"
R536A_PREREG = RESEARCH / "r536a_finalist_cluster_fdr_preregistration.json"
R537_ATTEMPT1 = RESEARCH / "r537_attempt1_context_path_failure.json"
R537_ATTEMPT2 = RESEARCH / "r537_attempt2_membership_path_failure.json"
R537_ATTEMPT3 = RESEARCH / "r537_attempt3_admitted_wrapper_failure.json"
GATE_POLICY = (
    DATA
    / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/gate_policy_v2.json"
)
PREREG = RESEARCH / "r537c_expanded_factor_portfolio_preregistration.json"
RESULT = RESEARCH / "r537c_expanded_factor_portfolio_result.json"
MARKDOWN = RESEARCH / "r537c_expanded_factor_portfolio_result.md"
TRAIN_START = date(2022, 5, 5)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
ENTRY_SCORE = 70.0
EXIT_SCORE = 40.0
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



def load_expanded_representatives(root: Path) -> list[dict[str, Any]]:
    root = Path(root)
    verify_sidecar(root / R536A_RESULT.relative_to(ROOT))
    verify_sidecar(root / R536A_PREREG.relative_to(ROOT))
    r536 = _load_script_module("research_r536_finalist_cluster_fdr")
    finalists = {row["family_id"]: row for row in r536.load_finalists(root)}
    source = json.loads(
        (root / R536A_RESULT.relative_to(ROOT)).read_text(encoding="utf-8")
    )
    out = []
    for cluster in source["metrics"]["representatives"]:
        finalist = finalists[cluster["family_id"]]
        selected = finalist["selected"]
        out.append({
            "family_id": cluster["family_id"],
            "cluster": cluster["cluster"],
            "cluster_size": cluster["cluster_size"],
            "parameter": selected["parameter"],
            "candidate": finalist["candidate"],
            "selected": selected,
            "train_icir": cluster["train_icir"],
        })
    return sorted(out, key=lambda row: row["family_id"])


def load_runtime(root: Path) -> dict[str, Any]:
    r533 = _load_script_module("research_r533_core_formal_portfolio")
    runtime = r533.load_runtime_inputs(root)
    r536 = _load_script_module("research_r536_finalist_cluster_fdr")
    r530 = runtime["study"]["r530"]
    r531 = runtime["study"]["r531"]
    representatives = load_expanded_representatives(root)
    candidates = {
        row["family_id"]: r531.Admitted(row["candidate"], row["selected"])
        for row in representatives
    }
    frames = {
        row["family_id"]: r530._factor_frame(
            row["candidate"], row["candidate"].parameter, runtime["study"]["context"]
        )
        for row in representatives
    }
    expanded_core = r531._core_frame(
        representatives,
        candidates,
        frames,
        runtime["study"]["membership"],
    )
    expanded_panel = r533.scaled_score_panel(expanded_core)
    control_panel = runtime["panel"].select(
        "symbol",
        "date",
        pl.col("score").alias("core_score"),
    )
    panel = expanded_panel.join(
        control_panel, on=["symbol", "date"], how="full", coalesce=True
    ).sort(["date", "symbol"])
    outside = panel.join(
        runtime["study"]["membership"], on=["symbol", "date"], how="anti"
    )
    if not outside.is_empty():
        raise RuntimeError(f"expanded panel has non-members: {outside.height}")
    return {
        "r533": r533,
        "runtime": runtime,
        "r536": r536,
        "p5": _load_script_module("research_factor_increment_p5"),
        "panel": panel,
        "symbols": runtime["symbols"],
        "representatives": representatives,
        "candidates": candidates,
        "score_rows": panel.height,
        "score_rows_within_membership": outside.is_empty(),
    }


def prepare_expanded_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
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
    prepared = service.prepare_matrix_optimization([seed])
    return p5._extend_prepared_with_panel(
        prepared,
        runtime["panel"],
        ["score", "core_score"],
    )


def run_expanded_window(
    p5: Any,
    service: Any,
    prepared: Any,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
    score_field: str = "score",
) -> dict[str, Any]:
    r533 = _load_script_module("research_r533_core_formal_portfolio")
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
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R537:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R537:{label}")
    if result.error:
        raise RuntimeError(f"R537:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
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


def run_confirmation(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    r533 = _load_script_module("research_r533_core_formal_portfolio")
    full_prepared = prepare_expanded_window(
        p5, service, runtime, start=TRAIN_START, end=END
    )
    full = {
        cost: run_expanded_window(
            p5,
            service,
            full_prepared,
            label=f"expanded_full_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_full = run_expanded_window(
        p5,
        service,
        full_prepared,
        label="core_full_2x",
        start=TRAIN_START,
        end=END,
        cost_label="2x",
        score_field="core_score",
    )
    validation_prepared = prepare_expanded_window(
        p5, service, runtime, start=VALIDATION_START, end=VALIDATION_END
    )
    validation = run_expanded_window(
        p5,
        service,
        validation_prepared,
        label="expanded_validation_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    recent_prepared = prepare_expanded_window(
        p5, service, runtime, start=RECENT_START, end=END
    )
    recent = {
        cost: run_expanded_window(
            p5,
            service,
            recent_prepared,
            label=f"expanded_recent_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
        for cost in ("2x", "3x", "5x")
    }
    control_recent = run_expanded_window(
        p5,
        service,
        recent_prepared,
        label="core_recent_2x",
        start=RECENT_START,
        end=END,
        cost_label="2x",
        score_field="core_score",
    )
    full["2x"]["bootstrap"] = r533.bootstrap_curve(full["2x"]["curve"], seed=53701)
    recent["2x"]["bootstrap"] = r533.bootstrap_curve(recent["2x"]["curve"], seed=53702)
    validation["bootstrap"] = r533.bootstrap_curve(validation["curve"], seed=53703)
    fresh = []
    for start in FRESH_STARTS:
        prepared = prepare_expanded_window(
            p5, service, runtime, start=start, end=END
        )
        run = run_expanded_window(
            p5,
            service,
            prepared,
            label=f"expanded_fresh_{start.isoformat()}_2x",
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
    confirmation: dict[str, Any],
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
        runtime["runtime"]["industries"],
        max_positions=MAX_POSITIONS,
    )
    concentration["net_to_gross"] = r533._net_to_gross(full_2x["trades"])
    all_runs = [
        full_2x,
        full_3x,
        full_5x,
        confirmation["validation"],
        recent_2x,
        recent_3x,
        recent_5x,
        control_full,
        control_recent,
    ]
    evidence_ok = r533.formal_evidence_pass(all_runs)
    checks = {
        "input_sidecars_valid": True,
        "r526_daily_membership": True,
        "expanded_score_rows_within_membership": runtime["score_rows_within_membership"],
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
        "id": "r537_expanded_13_factor",
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
    validation_positive = (
        confirmation["validation"]["stats"]["annual_return"] > 0
    )
    hard_pass = (
        evaluation["status"] == "qualified_for_paper_review"
        and validation_positive
    )
    failed = r533._failed_leaf_paths(evaluation["gates"])
    if not validation_positive:
        failed.append("validation_annual_positive")
    return {
        "candidate": candidate,
        "evaluation": evaluation,
        "validation_positive": validation_positive,
        "conclusion": "watch_only" if hard_pass else "rejected",
        "failed_gate_paths": failed,
        "formal_evidence": full_2x["formal_evidence"],
    }


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in row.items() if key not in {"curve", "trades"}
    }


def run_study(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    confirmation = run_confirmation(p5, service, runtime)
    gate = build_gate_payload(runtime, confirmation)
    payload = {
        "schema_version": 1,
        "research_id": "R537C",
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_r533a": R533A_RESULT.relative_to(ROOT).as_posix(),
        "source_r536a": R536A_RESULT.relative_to(ROOT).as_posix(),
        "expanded_representative_count": len(runtime["representatives"]),
        "expanded_representatives": [
            {
                "family_id": row["family_id"],
                "parameter": row["parameter"],
                "cluster": row["cluster"],
                "cluster_size": row["cluster_size"],
            }
            for row in runtime["representatives"]
        ],
        "score_rows": runtime["score_rows"],
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
            "R537 attempts 1-3 failed before any backtest due runtime-path and R531 wrapper mismatches; R537C is the explicit amendment.",
            "All 13 representatives were rejected by the strict R536A independent-cluster FDR; this is a portfolio-level combination test, not a reversal of that conclusion.",
            "The recent year has been observed repeatedly; R537 is not an independent discovery sample.",
            "Only the 10-position/60-day execution from R533A is used; no new parameter selection is performed.",
            "The project v2 gate policy treats 5x cost as warning-only, never as leverage or hard rejection.",
            "R526 membership and industry vintages are reconstructed research inputs.",
            "No leverage, orders, paper targets or active version are produced.",
        ],
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    return payload, runtime


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R537 13 独立代表扩展因子组合",
        "",
        f"- 代表数：{payload['expanded_representative_count']}",
        "- 构造：13 因子等权 rank；control：R533 6 因子核心",
        "",
        "## 确认结果",
        "",
        "| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    confirmation = payload["confirmation"]
    for label, row in (
        ("full 2x", confirmation["full"]["2x"]),
        ("full 3x", confirmation["full"]["3x"]),
        ("full 5x", confirmation["full"]["5x"]),
        ("validation 2x", confirmation["validation"]),
        ("recent 2x", confirmation["recent"]["2x"]),
        ("recent 3x", confirmation["recent"]["3x"]),
        ("recent 5x", confirmation["recent"]["5x"]),
        ("core full 2x", confirmation["control_full"]),
        ("core recent 2x", confirmation["control_recent"]),
    ):
        stats = row["stats"]
        lines.append(
            f"| {label} | {stats['annual_return']} | {stats['sharpe']} | "
            f"{stats['max_drawdown']} | {stats['total_return']} | {row['trade_count']} |"
        )
    lines += ["", "## 年度和起始相位", ""]
    lines += [f"- {year}: {value}" for year, value in confirmation["yearly"].items()]
    lines += [
        f"- fresh {row['start']}: {row['total_return']}"
        for row in confirmation["fresh_starts"]
    ]
    lines += [
        "",
        "## 门禁",
        "",
        f"- 总体结论：{payload['conclusion']}",
        f"- 失败路径：{payload['gate']['failed_gate_paths']}",
        f"- v2 状态：{payload['gate']['evaluation']['status']}",
        f"- 5× warnings：{payload['gate']['evaluation']['diagnostics']['5x_warnings']}",
        f"- 5× 硬否决：{payload['gate']['evaluation']['diagnostics']['5x_hard_rejection']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
    ]
    return "\n".join(lines) + "\n"


def _verify_runner_binding(root: Path) -> None:
    if not PREREG.is_file():
        raise FileNotFoundError(f"R537 preregistration is missing: {PREREG}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R537 runner hash does not match frozen preregistration")


def freeze_preregistration(root: Path) -> Path:
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R537 preregistration already exists: {PREREG}")
    root = Path(root)
    reps = load_expanded_representatives(root)
    payload = {
        "schema_version": 1,
        "research_id": "R537C",
        "research_type": RESEARCH_TYPE,
        "question": "Does an equal-weight portfolio of the 13 independent R536A representatives improve the formal R533 core portfolio after validation and recent-year confirmation?",
        "hypothesis": "Broader independent factor breadth improves full-period risk-adjusted return without sacrificing the recent-year profile.",
        "study_scope": "research_only",
        "runner": Path(__file__).resolve().relative_to(root).as_posix(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {
            "r533a_result": sha256(root / R533A_RESULT.relative_to(ROOT)),
            "r533a_preregistration": sha256(root / R533A_PREREG.relative_to(ROOT)),
            "r536a_result": sha256(root / R536A_RESULT.relative_to(ROOT)),
            "r536a_preregistration": sha256(root / R536A_PREREG.relative_to(ROOT)),
            "r526_membership": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/daily_snapshots.parquet"),
            "r526_industry": sha256(root / DATA / "factor_research/research/r526_unified_pit_bundle_window/industry_history.parquet"),
            "gate_policy_v2": sha256(root / GATE_POLICY.relative_to(ROOT)),
            "r537_attempt1_failure": sha256(root / R537_ATTEMPT1.relative_to(ROOT)),
            "r537_attempt2_failure": sha256(root / R537_ATTEMPT2.relative_to(ROOT)),
            "r537_attempt3_failure": sha256(root / R537_ATTEMPT3.relative_to(ROOT)),
        },
        "parameters": {
            "expanded_representatives": [
                {
                    "family_id": row["family_id"],
                    "parameter": row["parameter"],
                    "cluster": row["cluster"],
                }
                for row in reps
            ],
            "aggregation": "equal-weight percentile rank",
            "control": "R533A six-factor core",
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "entry_score": ENTRY_SCORE,
            "exit_score": EXIT_SCORE,
            "weight_search": False,
        },
        "sample": {
            "start": str(TRAIN_START),
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
            "validation_annual_positive": True,
            "gate_policy_v2": GATE_POLICY.relative_to(ROOT).as_posix(),
            "five_x_hard_rejection": False,
        },
        "falsification_checks": [
            "r536a_cluster_representatives_only",
            "no_weight_or_parameter_search",
            "formal_evidence_saved_lookahead_t1",
            "gate_policy_v2_layered_thresholds",
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
        raise FileExistsError(f"R537 result already exists: {RESULT}")
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
            "source_r536a": payload["source_r536a"],
            "expanded_representative_count": payload["expanded_representative_count"],
            "expanded_representatives": payload["expanded_representatives"],
            "score_rows": payload["score_rows"],
            "confirmation": payload["confirmation"],
        },
        "source_result_hashes": [
            sha256(root / R533A_RESULT.relative_to(ROOT)),
            sha256(root / R536A_RESULT.relative_to(ROOT)),
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
        operation="r537c_expanded_factor_portfolio_published",
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
        reps = load_expanded_representatives(ROOT)
        print(json.dumps({
            "representative_count": len(reps),
            "representatives": [
                {"family_id": row["family_id"], "parameter": row["parameter"]}
                for row in reps
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    _verify_runner_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R537 results already exist; refusing overwrite")
    payload, _runtime = run_study(ROOT)
    published = publish_result(ROOT, payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "failed_gate_paths": payload["gate"]["failed_gate_paths"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
