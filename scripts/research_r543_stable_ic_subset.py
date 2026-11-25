#!/usr/bin/env python3
"""R543: test a train+validation stable-IC subset of R539C."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r543_stable_ic_subset_preregistration.json"
)
RESEARCH_ID = "R543"
RUNNER = "scripts/research_r543_stable_ic_subset.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R542_RESULT = RESEARCH / "r542_recent_failure_attribution_result.json"
R542_PREREG = RESEARCH / "r542_recent_failure_attribution_preregistration.json"
R542_RUNNER = ROOT / "scripts/research_r542_recent_failure_attribution.py"
R539_RESULT = RESEARCH / "r539_exit_risk_grid_result.json"
R539_PREREG = RESEARCH / "r539_exit_risk_grid_preregistration.json"
PREREG = RESEARCH / "r543_stable_ic_subset_preregistration.json"
RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
MARKDOWN = RESEARCH / "r543_stable_ic_subset_result.md"
TRAIN_START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
MAX_POSITIONS = 10
MAX_HOLD_DAYS = 60
INDUSTRY_CAP = 3
STOP_LOSS = -0.12
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
EXPECTED_MEMBERS = (
    "amount_level",
    "bollinger_width",
    "dividend_yield_quantile",
    "fund_holding_ratio_absolute",
    "max_return",
    "turnover_level",
    "vol_of_vol",
)
EXPECTED_EXCLUDED = (
    "fund_count_quantile",
    "lhb_inst_net_buy_event",
    "lhb_net_buy_holding",
    "market_down_beta",
    "repurchase_event_decay",
    "repurchase_holding",
)


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
    sys.modules[name] = module
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


def select_stable_factors(rows: list[dict[str, Any]]) -> list[str]:
    selected = []
    for row in rows:
        train = row.get("train_ic")
        validation = row.get("validation_ic")
        if (
            isinstance(train, (int, float))
            and isinstance(validation, (int, float))
            and float(train) > 0.0
            and float(validation) > 0.0
        ):
            selected.append(str(row["family_id"]))
    return sorted(selected)


def fresh_start_gate(values: list[float]) -> dict[str, Any]:
    if len(values) != 6:
        raise ValueError("fresh-start gate requires exactly six values")
    positive_count = sum(float(value) > 0 for value in values)
    median = float(st.median(values))
    return {
        "positive_count": positive_count,
        "median": median,
        "pass": positive_count >= 4 and median > 0,
    }


def candidate_gate(
    *,
    full: dict[str, Any],
    recent: dict[str, Any],
    validation: dict[str, Any],
    fresh_values: list[float],
    stress_3x: dict[str, Any],
    warning_5x: dict[str, Any],
) -> dict[str, Any]:
    fresh = fresh_start_gate(fresh_values)
    checks = {
        "full_annual_min": float(full["annual_return"]) >= 0.10,
        "full_sharpe_min": float(full["sharpe"]) >= 0.90,
        "full_mdd_min": float(full["max_drawdown"]) >= -0.20,
        "recent_return_strictly_positive": float(recent["total_return"]) > 0.0,
        "validation_annual_positive": float(validation["annual_return"]) > 0.0,
        "fresh_starts_pass": bool(fresh["pass"]),
    }
    stress_checks = {
        "stress_3x_annual_min": float(stress_3x["annual_return"]) >= 0.08,
        "stress_3x_mdd_min": float(stress_3x["max_drawdown"]) >= -0.22,
        "stress_3x_recent_return_min": float(stress_3x["recent_total_return"]) >= -0.03,
    }
    five_warnings = []
    if float(warning_5x["annual_return"]) < 0:
        five_warnings.append("negative_5x_annual")
    if float(warning_5x["max_drawdown"]) < float(stress_3x["max_drawdown"]) - 0.05:
        five_warnings.append("drawdown_deterioration_gt_5pp")
    core_pass = all(checks.values())
    stress_pass = all(stress_checks.values())
    failed = [key for key, value in {**checks, **stress_checks}.items() if not value]
    conclusion = "rejected" if not core_pass or not stress_pass else "watch_only"
    return {
        "core_checks": checks,
        "stress_checks": stress_checks,
        "fresh_start": fresh,
        "core_pass": core_pass,
        "stress_pass": stress_pass,
        "five_x_hard_rejection": False,
        "five_x_warnings": five_warnings,
        "failed_gates": failed,
        "conclusion": conclusion,
    }


def scientific_design() -> dict[str, Any]:
    return {
        "selection_rule": "train_ic_gt_0_and_validation_ic_gt_0",
        "members": list(EXPECTED_MEMBERS),
        "excluded": list(EXPECTED_EXCLUDED),
        "formal_runs": 13,
        "recent_is_independent_oos": False,
        "windows": {
            "train_start": str(TRAIN_START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "max_positions": MAX_POSITIONS,
            "max_hold_days": MAX_HOLD_DAYS,
            "industry_cap": INDUSTRY_CAP,
            "stop_loss": STOP_LOSS,
            "trailing_stop": None,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_policy": "warning_only",
        },
        "gates": {
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.20,
            "recent_return_strictly_positive": True,
            "validation_annual_positive": True,
            "fresh_positive_min": 4,
            "fresh_median_positive": True,
            "stress_3x_annual_min": 0.08,
            "stress_3x_mdd_min": -0.22,
            "stress_3x_recent_return_min": -0.03,
            "five_x_hard_rejection": False,
        },
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def freeze_preregistration(root: Path) -> Path:
    prereg = root / RESEARCH_PREREGISTRATION
    if prereg.exists() or prereg.with_name(prereg.name + ".sha256").exists():
        raise FileExistsError(f"R543 preregistration already exists: {prereg}")
    sources = {
        "r542_result": root / R542_RESULT.relative_to(ROOT),
        "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
        "r542_runner": root / R542_RUNNER.relative_to(ROOT),
        "r539_result": root / R539_RESULT.relative_to(ROOT),
        "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Does a train+validation stable-IC seven-factor subset improve the "
            "absolute recent-year return while retaining acceptable full-period risk?"
        ),
        "hypothesis": (
            "Removing factors whose train or validation directional IC is non-positive "
            "will improve recent-year robustness without changing the execution skeleton."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": {name: sha256(path) for name, path in sources.items()},
        "parameters": scientific_design(),
        "sample": scientific_design()["windows"],
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "selection_uses_train_and_validation_only",
            "formal_evidence_saved_lookahead_t1",
            "recent_is_not_independent_oos",
            "all_13_planned_runs_completed",
            "five_x_warning_only",
            "no_orders_or_active_version",
        ],
        "output_policy": scientific_design()["output_policy"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _write_immutable(prereg, raw)
    return prereg


def _verify_binding(root: Path) -> None:
    prereg = json.loads((root / RESEARCH_PREREGISTRATION).read_text(encoding="utf-8"))
    verify_sidecar(root / RESEARCH_PREREGISTRATION)
    if prereg["runner_sha256"] != sha256(Path(__file__).resolve()):
        raise RuntimeError("R543 runner hash no longer matches preregistration")
    for name, expected in prereg["input_hashes"].items():
        path = {
            "r542_result": root / R542_RESULT.relative_to(ROOT),
            "r542_preregistration": root / R542_PREREG.relative_to(ROOT),
            "r542_runner": root / R542_RUNNER.relative_to(ROOT),
            "r539_result": root / R539_RESULT.relative_to(ROOT),
            "r539_preregistration": root / R539_PREREG.relative_to(ROOT),
        }[name]
        if sha256(path) != expected:
            raise RuntimeError(f"R543 input changed: {name}")


def run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: Any,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
    variant: dict[str, Any],
) -> dict[str, Any]:
    local_runtime = {**runtime, "panel": panel}
    prepared = runtime["r539"].prepare_exit_window(
        p5,
        service,
        local_runtime,
        variant,
        start=start,
        end=end,
    )
    config = runtime["r533"].build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=MAX_POSITIONS,
        max_hold_days=MAX_HOLD_DAYS,
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": STOP_LOSS,
        "trailing_stop": None,
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": INDUSTRY_CAP,
    }
    config.max_exposure_pct = 1.0
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R543:{label}: prepared signature mismatch")
    result = service.run_with_evidence(config, prepared=prepared)
    evidence = require_formal_evidence(result, label=f"R543:{label}")
    if result.error:
        raise RuntimeError(f"R543:{label}: {result.error}")
    trades = [
        trade if isinstance(trade, dict) else service._trade_to_dict(trade)
        for trade in result.trades
    ]
    return {
        "label": label,
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
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def load_candidate(runtime: dict[str, Any], r542: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = json.loads(R542_RESULT.read_text(encoding="utf-8"))
    factor_ic = source["metrics"]["factor_ic"]
    selected = select_stable_factors(factor_ic)
    if selected != sorted(EXPECTED_MEMBERS):
        raise RuntimeError(
            f"R543 stable factor rule changed: expected {sorted(EXPECTED_MEMBERS)}, got {selected}"
        )
    representatives = [
        row for row in runtime["representatives"] if row["family_id"] in selected
    ]
    if len(representatives) != 7:
        raise RuntimeError("R543 candidate must contain exactly seven representatives")
    panel = r542.build_member_panel(runtime, representatives)
    return representatives, panel


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    representatives, panel = load_candidate(runtime, r542)
    variant = r542._variant(runtime, "stop_12")
    full = {
        cost: run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"full_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
            variant=variant,
        )
        for cost in ("2x", "3x", "5x")
    }
    recent = {
        cost: run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"recent_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
            variant=variant,
        )
        for cost in ("2x", "3x", "5x")
    }
    validation = run_window(
        p5,
        service,
        runtime,
        panel,
        label="validation_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
        variant=variant,
    )
    fresh = []
    for start in FRESH_STARTS:
        run = run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"fresh_{start.isoformat()}_2x",
            start=start,
            end=END,
            cost_label="2x",
            variant=variant,
        )
        fresh.append({"start": str(start), **_public_run(run)})
    formal_runs = [
        *full.values(),
        *recent.values(),
        validation,
        *(run for row in fresh for run in [row]),
    ]
    if len(formal_runs) != 13:
        raise RuntimeError(f"R543 expected 13 formal runs, got {len(formal_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in formal_runs
    )
    full_2x = full["2x"]
    recent_2x = recent["2x"]
    full_2x["bootstrap"] = runtime["r533"].bootstrap_curve(
        full_2x["curve"], seed=54301
    )
    recent_2x["bootstrap"] = runtime["r533"].bootstrap_curve(
        recent_2x["curve"], seed=54302
    )
    source = json.loads(R542_RESULT.read_text(encoding="utf-8"))
    baseline = source["metrics"]["baseline"]
    benchmarks = source["metrics"]["benchmarks"]
    concentration = runtime["r533"].concentration_metrics(
        full_2x["trades"],
        runtime["industries"],
        max_positions=MAX_POSITIONS,
    )
    concentration["net_to_gross"] = runtime["r533"]._net_to_gross(full_2x["trades"])
    gate = candidate_gate(
        full=full_2x["stats"],
        recent=recent_2x["stats"],
        validation=validation["stats"],
        fresh_values=[float(row["stats"]["total_return"]) for row in fresh],
        stress_3x={
            **full["3x"]["stats"],
            "recent_total_return": recent["3x"]["stats"]["total_return"],
        },
        warning_5x={
            "annual_return": full["5x"]["stats"]["annual_return"],
            "max_drawdown": full["5x"]["stats"]["max_drawdown"],
        },
    )
    if not evidence_ok:
        gate["failed_gates"].append("formal_evidence")
        gate["conclusion"] = "rejected"
    comparison = {
        "baseline_full_2x": baseline["full"]["stats"],
        "baseline_recent_2x": baseline["recent"]["stats"],
        "full_annual_delta_vs_baseline": round(
            float(full_2x["stats"]["annual_return"])
            - float(baseline["full"]["stats"]["annual_return"]),
            8,
        ),
        "full_sharpe_delta_vs_baseline": round(
            float(full_2x["stats"]["sharpe"])
            - float(baseline["full"]["stats"]["sharpe"]),
            8,
        ),
        "recent_return_delta_vs_baseline": round(
            float(recent_2x["stats"]["total_return"])
            - float(baseline["recent"]["stats"]["total_return"]),
            8,
        ),
        "recent_mdd_delta_vs_baseline": round(
            float(recent_2x["stats"]["max_drawdown"])
            - float(baseline["recent"]["stats"]["max_drawdown"]),
            8,
        ),
        "hs300_recent": benchmarks["hs300"]["recent_total_return"],
        "equal_weight_recent": benchmarks["equal_weight_pit_members"]["recent_total_return"],
        "recent_vs_hs300": round(
            float(recent_2x["stats"]["total_return"])
            - float(benchmarks["hs300"]["recent_total_return"]),
            8,
        ),
        "recent_vs_equal_weight": round(
            float(recent_2x["stats"]["total_return"])
            - float(benchmarks["equal_weight_pit_members"]["recent_total_return"]),
            8,
        ),
    }
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "members": [row["family_id"] for row in representatives],
        "excluded": [
            row["family_id"] for row in runtime["representatives"]
            if row["family_id"] not in {member["family_id"] for member in representatives}
        ],
        "full": {cost: _public_run(row) for cost, row in full.items()},
        "recent": {cost: _public_run(row) for cost, row in recent.items()},
        "validation": _public_run(validation),
        "fresh_starts": fresh,
        "bootstrap": {
            "full_2x": full_2x["bootstrap"],
            "recent_2x": recent_2x["bootstrap"],
        },
        "comparison": comparison,
        "concentration": concentration,
        "gate": gate,
        "formal_runs": [_public_run(run) for run in formal_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "R542 recent-year results were already observed; R543 recent is not independent OOS.",
            "This round changes only the factor member set; execution parameters remain frozen.",
            "The seven-factor rule uses train and validation IC only, but factor observation can still create selection bias.",
            "The 7-factor equal-weight mean-available composite can differ from a missing-data-penalized weighted composite.",
            "Five-times cost is warning-only and no leverage is used.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": (
            [] if gate["conclusion"] == "watch_only" else gate["failed_gates"]
        ),
        "output_policy": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    full = payload["full"]["2x"]["stats"]
    recent = payload["recent"]["2x"]["stats"]
    validation = payload["validation"]["stats"]
    comparison = payload["comparison"]
    gate = payload["gate"]
    fresh = payload["fresh_starts"]
    lines = [
        "# R543 稳定 IC 因子子集",
        "",
        "> 这是非独立确认：R542 已观察 recent，结果最多为 `watch_only`。",
        "",
        "## 因子集合",
        "",
        f"- 纳入：{', '.join(f'`{item}`' for item in payload['members'])}",
        f"- 排除：{', '.join(f'`{item}`' for item in payload['excluded'])}",
        "- 规则：train IC > 0 且 validation IC > 0；recent 不参与筛选。",
        "",
        "## 核心结果",
        "",
        "| 窗口 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| full 2x | {full['annual_return']:.2%} | {full['sharpe']:.2f} | {full['max_drawdown']:.2%} | {full['total_return']:.2%} | {int(full['n_trades'])} |",
        f"| recent 2x | {recent['annual_return']:.2%} | {recent['sharpe']:.2f} | {recent['max_drawdown']:.2%} | {recent['total_return']:.2%} | {int(recent['n_trades'])} |",
        f"| validation 2x | {validation['annual_return']:.2%} | {validation['sharpe']:.2f} | {validation['max_drawdown']:.2%} | {validation['total_return']:.2%} | {int(validation['n_trades'])} |",
        "",
        "## 与 baseline 的取舍",
        "",
        f"- full 年化差：{comparison['full_annual_delta_vs_baseline']:+.2%}",
        f"- full Sharpe 差：{comparison['full_sharpe_delta_vs_baseline']:+.2f}",
        f"- recent 收益差：{comparison['recent_return_delta_vs_baseline']:+.2%}",
        f"- recent MDD 差：{comparison['recent_mdd_delta_vs_baseline']:+.2%}",
        f"- recent 对沪深300：{comparison['recent_vs_hs300']:+.2%}",
        f"- recent 对 PIT 等权：{comparison['recent_vs_equal_weight']:+.2%}",
        "",
        "## 成本压力",
        "",
        "| 成本 | full 年化 | full Sharpe | full MDD | recent 总收益 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for cost in ("2x", "3x", "5x"):
        stats = payload["full"][cost]["stats"]
        recent_stats = payload["recent"][cost]["stats"]
        lines.append(
            f"| {cost} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} | "
            f"{stats['max_drawdown']:.2%} | {recent_stats['total_return']:.2%} |"
        )
    lines += [
        "",
        "## Fresh starts",
        "",
        "| 起始日 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in fresh:
        stats = row["stats"]
        lines.append(
            f"| {row['start']} | {stats['total_return']:.2%} | {stats['annual_return']:.2%} | "
            f"{stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- 核心通过：{gate['core_pass']}",
        f"- 3× 压力通过：{gate['stress_pass']}",
        f"- Fresh-start：{gate['fresh_start']}",
        f"- 5× warnings：{gate['five_x_warnings']}",
        f"- 失败项：{gate['failed_gates']}",
        f"- 结论：`{payload['conclusion']}`",
        "",
        "## Bootstrap",
        "",
        f"- full 2x：{payload['bootstrap']['full_2x']}",
        f"- recent 2x：{payload['bootstrap']['recent_2x']}",
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in payload["known_limits"]],
        "",
    ]
    return "\n".join(lines)


def publish_result(payload: dict[str, Any]) -> dict[str, Any]:
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
        "gate_results": payload["gate"],
        "metrics": {
            "design": payload["design"],
            "members": payload["members"],
            "excluded": payload["excluded"],
            "full": payload["full"],
            "recent": payload["recent"],
            "validation": payload["validation"],
            "fresh_starts": payload["fresh_starts"],
            "bootstrap": payload["bootstrap"],
            "comparison": payload["comparison"],
            "concentration": payload["concentration"],
            "formal_runs": payload["formal_runs"],
        },
        "source_result_hashes": [sha256(R542_RESULT), sha256(R539_RESULT)],
        "formal_evidence": payload["full"]["2x"]["formal_evidence"],
        "known_limits": payload["known_limits"],
        "negative_knowledge": payload["negative_knowledge"],
        "output_policy": payload["output_policy"],
    })
    published = publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r543_stable_ic_subset_published",
    )
    return published.__dict__


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
        print(json.dumps(scientific_design(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _verify_binding(ROOT)
    if RESULT.exists() or MARKDOWN.exists():
        raise FileExistsError("R543 results already exist; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "full_2x": payload["full"]["2x"]["stats"],
        "recent_2x": payload["recent"]["2x"]["stats"],
        "validation_2x": payload["validation"]["stats"],
        "fresh_start": payload["gate"]["fresh_start"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
