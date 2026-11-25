#!/usr/bin/env python3
"""R546: confirm the vol_12 risk budget without further parameter search."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
    "data/factor_research/research/r546_vol12_confirmation_preregistration.json"
)
RESEARCH_ID = "R546"
RUNNER = "scripts/research_r546_vol12_confirmation.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R545_RESULT = RESEARCH / "r545_stable7_risk_budget_result.json"
R545_PREREG = RESEARCH / "r545_stable7_risk_budget_preregistration.json"
R545_RUNNER = ROOT / "scripts/research_r545_stable7_risk_budget.py"
R543_RESULT = RESEARCH / "r543_stable_ic_subset_result.json"
R543_PREREG = RESEARCH / "r543_stable_ic_subset_preregistration.json"
R543_RUNNER = ROOT / "scripts/research_r543_stable_ic_subset.py"
R544_RESULT = RESEARCH / "r544_stable7_parameter_mc_result.json"
R542_RESULT = RESEARCH / "r542_recent_failure_attribution_result.json"
PREREG = RESEARCH / "r546_vol12_confirmation_preregistration.json"
RESULT = RESEARCH / "r546_vol12_confirmation_result.json"
MARKDOWN = RESEARCH / "r546_vol12_confirmation_result.md"
TRAIN_START = date(2022, 5, 5)
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
VARIANT = {
    "id": "vol_12",
    "kind": "vol_target",
    "target": 0.12,
    "lookback": 60,
    "floor": 0.50,
    "order": 7,
}
BOOTSTRAP_RUNS = 5000
BOOTSTRAP_BLOCK = 20
BOOTSTRAP_SEED = 546


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


def candidate_gate(
    *,
    full: dict[str, Any],
    recent: dict[str, Any],
    validation: dict[str, Any],
    fresh_values: list[float],
    stress_3x: dict[str, Any],
    warning_5x: dict[str, Any],
) -> dict[str, Any]:
    r543 = _load_module("research_r543_stable_ic_subset")
    return r543.candidate_gate(
        full=full,
        recent=recent,
        validation=validation,
        fresh_values=fresh_values,
        stress_3x=stress_3x,
        warning_5x=warning_5x,
    )


def scientific_design() -> dict[str, Any]:
    return {
        "variant": dict(VARIANT),
        "members": list(EXPECTED_MEMBERS),
        "confirmation_runs": 13,
        "formal_runs": 13,
        "bootstrap_runs": BOOTSTRAP_RUNS,
        "bootstrap_block": BOOTSTRAP_BLOCK,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "windows": {
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "recent_is_independent_oos": False,
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
            "five_x_policy": "warning_only",
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
        raise FileExistsError(f"R546 preregistration already exists: {prereg}")
    sources = {
        "r545_result": root / R545_RESULT.relative_to(ROOT),
        "r545_preregistration": root / R545_PREREG.relative_to(ROOT),
        "r545_runner": root / R545_RUNNER.relative_to(ROOT),
        "r543_result": root / R543_RESULT.relative_to(ROOT),
        "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
        "r543_runner": root / R543_RUNNER.relative_to(ROOT),
        "r544_result": root / R544_RESULT.relative_to(ROOT),
        "r542_result": root / R542_RESULT.relative_to(ROOT),
    }
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Does the R545 vol_12 risk budget retain positive recent and validation "
            "returns while restoring the full-period annual return above 10%?"
        ),
        "hypothesis": (
            "A higher volatility target than R545 vol_10 will improve annual return "
            "without violating the drawdown and stress-cost constraints."
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
        "gates": {
            "confirmation_runs": 13,
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "variant": VARIANT,
            "five_x_hard_rejection": False,
            "recent_is_independent_oos": False,
        },
        "falsification_checks": [
            "fixed_vol12_only_no_search",
            "causal_position_policy_context",
            "budget_scale_never_exceeds_one",
            "formal_evidence_saved_lookahead_t1",
            "five_x_warning_only",
            "recent_not_independent_oos",
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
        raise RuntimeError("R546 runner hash no longer matches preregistration")
    for name, expected in prereg["input_hashes"].items():
        path = {
            "r545_result": root / R545_RESULT.relative_to(ROOT),
            "r545_preregistration": root / R545_PREREG.relative_to(ROOT),
            "r545_runner": root / R545_RUNNER.relative_to(ROOT),
            "r543_result": root / R543_RESULT.relative_to(ROOT),
            "r543_preregistration": root / R543_PREREG.relative_to(ROOT),
            "r543_runner": root / R543_RUNNER.relative_to(ROOT),
            "r544_result": root / R544_RESULT.relative_to(ROOT),
            "r542_result": root / R542_RESULT.relative_to(ROOT),
        }[name]
        if sha256(path) != expected:
            raise RuntimeError(f"R546 input changed: {name}")


def run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    prepared: Any,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
) -> dict[str, Any]:
    r545 = _load_module("research_r545_stable7_risk_budget")
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
    config.position_sizing = "policy"
    config.position_policy = VARIANT["id"]
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R546:{label}: prepared signature mismatch")
    with r545.policy_injection(VARIANT):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R546:{label}")
        if result.error:
            raise RuntimeError(f"R546:{label}: {result.error}")
        trades = [
            trade if isinstance(trade, dict) else service._trade_to_dict(trade)
            for trade in result.trades
        ]
    return {
        "label": label,
        "start": str(start),
        "end": str(end),
        "cost": cost_label,
        "variant": dict(VARIANT),
        "stats": p5._select_stats(result.stats),
        "trade_count": len(trades),
        "curve": list(result.equity_curve),
        "trades": trades,
        "formal_evidence": runtime["r533"]._evidence_summary(evidence),
    }


def public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": run["label"],
        "start": run["start"],
        "end": run["end"],
        "cost": run["cost"],
        "variant": run["variant"],
        "stats": run["stats"],
        "trade_count": run["trade_count"],
        "formal_evidence": run["formal_evidence"],
    }


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    r543 = _load_module("research_r543_stable_ic_subset")
    r545 = _load_module("research_r545_stable7_risk_budget")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    r543_source = json.loads(R543_RESULT.read_text(encoding="utf-8"))
    r545_source = json.loads(R545_RESULT.read_text(encoding="utf-8"))
    if r543_source.get("conclusion") != "watch_only":
        raise RuntimeError("R543 source conclusion changed")
    if r545_source.get("conclusion") != "rejected":
        raise RuntimeError("R545 source conclusion changed")
    if set(r543_source["metrics"]["members"]) != set(EXPECTED_MEMBERS):
        raise RuntimeError("stable-seven member set changed")
    _representatives, panel = r543.load_candidate(runtime, r542)
    full = {}
    recent = {}
    for cost in ("2x", "3x", "5x"):
        prepared = r545.prepare_panel(
            p5,
            service,
            runtime,
            panel,
            start=TRAIN_START,
            end=END,
        )
        full[cost] = run_window(
            p5,
            service,
            runtime,
            prepared,
            label=f"full_vol12_{cost}",
            start=TRAIN_START,
            end=END,
            cost_label=cost,
        )
        prepared_recent = r545.prepare_panel(
            p5,
            service,
            runtime,
            panel,
            start=RECENT_START,
            end=END,
        )
        recent[cost] = run_window(
            p5,
            service,
            runtime,
            prepared_recent,
            label=f"recent_vol12_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
    prepared_validation = r545.prepare_panel(
        p5,
        service,
        runtime,
        panel,
        start=VALIDATION_START,
        end=VALIDATION_END,
    )
    validation = run_window(
        p5,
        service,
        runtime,
        prepared_validation,
        label="validation_vol12_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    fresh = []
    for start in FRESH_STARTS:
        prepared = r545.prepare_panel(
            p5,
            service,
            runtime,
            panel,
            start=start,
            end=END,
        )
        fresh.append(
            run_window(
                p5,
                service,
                runtime,
                prepared,
                label=f"fresh_{start.isoformat()}_vol12_2x",
                start=start,
                end=END,
                cost_label="2x",
            )
        )
    formal_runs = [*full.values(), *recent.values(), validation, *fresh]
    if len(formal_runs) != 13:
        raise RuntimeError(f"R546 expected 13 formal runs, got {len(formal_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in formal_runs
    )
    gate = candidate_gate(
        full=full["2x"]["stats"],
        recent=recent["2x"]["stats"],
        validation=validation["stats"],
        fresh_values=[run["stats"]["total_return"] for run in fresh],
        stress_3x={
            **full["3x"]["stats"],
            "recent_total_return": recent["3x"]["stats"]["total_return"],
        },
        warning_5x={
            **full["5x"]["stats"],
            "recent_total_return": recent["5x"]["stats"]["total_return"],
        },
    )
    if float(recent["5x"]["stats"]["total_return"]) < 0:
        gate["five_x_warnings"].append("negative_5x_recent")
    gate["formal_evidence"] = evidence_ok
    if not evidence_ok:
        gate["failed_gates"].append("formal_evidence")
        gate["conclusion"] = "rejected"
    full["2x"]["bootstrap"] = runtime["r533"].bootstrap_curve(
        full["2x"]["curve"], seed=54601
    )
    recent["2x"]["bootstrap"] = runtime["r533"].bootstrap_curve(
        recent["2x"]["curve"], seed=54602
    )
    comparison = {
        "r543_full_2x": r543_source["metrics"]["full"]["2x"],
        "r543_recent_2x": r543_source["metrics"]["recent"]["2x"],
        "r545_vol10_full_2x": r545_source["metrics"]["confirmation"]["full"]["2x"],
        "r545_vol10_recent_2x": r545_source["metrics"]["confirmation"]["recent"]["2x"],
        "r546_full_2x": full["2x"]["stats"],
        "r546_recent_2x": recent["2x"]["stats"],
        "full_annual_delta_vs_r543": round(
            float(full["2x"]["stats"]["annual_return"])
            - float(r543_source["metrics"]["full"]["2x"]["stats"]["annual_return"]),
            8,
        ),
        "recent_return_delta_vs_r543": round(
            float(recent["2x"]["stats"]["total_return"])
            - float(r543_source["metrics"]["recent"]["2x"]["stats"]["total_return"]),
            8,
        ),
        "full_mdd_delta_vs_r543": round(
            float(full["2x"]["stats"]["max_drawdown"])
            - float(r543_source["metrics"]["full"]["2x"]["stats"]["max_drawdown"]),
            8,
        ),
        "full_annual_delta_vs_r545_vol10": round(
            float(full["2x"]["stats"]["annual_return"])
            - float(r545_source["metrics"]["confirmation"]["full"]["2x"]["stats"]["annual_return"]),
            8,
        ),
    }
    benchmarks = json.loads(R542_RESULT.read_text(encoding="utf-8"))["metrics"]["benchmarks"]
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "members": list(EXPECTED_MEMBERS),
        "full": {cost: public_run(run) for cost, run in full.items()},
        "recent": {cost: public_run(run) for cost, run in recent.items()},
        "validation": public_run(validation),
        "fresh_starts": [public_run(run) for run in fresh],
        "bootstrap": {
            "full_2x": full["2x"]["bootstrap"],
            "recent_2x": recent["2x"]["bootstrap"],
        },
        "comparison": comparison,
        "benchmarks": benchmarks,
        "gate": gate,
        "formal_runs": [public_run(run) for run in formal_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "R545 and prior recent-year results were already observed; this is not independent OOS.",
            "Only the pre-declared vol_12 budget is confirmed; no parameter search is performed.",
            "The policy uses causal selected-candidate recent returns and portfolio drawdown only.",
            "Five-times cost is warning-only and no leverage is used.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": [] if gate["conclusion"] == "watch_only" else gate["failed_gates"],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    full = payload["full"]
    recent = payload["recent"]
    validation = payload["validation"]
    lines = [
        "# R546 vol_12 风险预算确认",
        "",
        "> R545 已观察 recent；本轮是非独立确认，最多 `watch_only`。",
        "",
        "## 结果",
        "",
        "| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 平均敞口 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, run, key in (
        ("full 2×", full["2x"], "stats"),
        ("full 3×", full["3x"], "stats"),
        ("full 5×", full["5x"], "stats"),
        ("recent 2×", recent["2x"], "stats"),
        ("recent 3×", recent["3x"], "stats"),
        ("recent 5×", recent["5x"], "stats"),
        ("validation 2×", validation, "stats"),
    ):
        stats = run[key]
        lines.append(
            f"| {label} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} | "
            f"{stats['max_drawdown']:.2%} | {stats['total_return']:.2%} | "
            f"{stats['avg_exposure']:.2%} | {int(stats['n_trades'])} |"
        )
    lines += [
        "",
        "## 与对照",
        "",
        f"- Full 年化较 R543：{payload['comparison']['full_annual_delta_vs_r543']:+.2%}",
        f"- Recent 较 R543：{payload['comparison']['recent_return_delta_vs_r543']:+.2%}",
        f"- Full MDD 较 R543：{payload['comparison']['full_mdd_delta_vs_r543']:+.2%}",
        f"- Full 年化较 R545 vol_10：{payload['comparison']['full_annual_delta_vs_r545_vol10']:+.2%}",
        "",
        "## Fresh starts",
        "",
        "| 起始日 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for run in payload["fresh_starts"]:
        stats = run["stats"]
        lines.append(
            f"| {run['start']} | {stats['total_return']:.2%} | {stats['annual_return']:.2%} | "
            f"{stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- Core：{payload['gate']['core_pass']}",
        f"- 3× stress：{payload['gate']['stress_pass']}",
        f"- Fresh-start：{payload['gate']['fresh_start']}",
        f"- 5× warnings：{payload['gate']['five_x_warnings']}",
        f"- 失败项：{payload['gate']['failed_gates']}",
        f"- 结论：`{payload['conclusion']}`",
        "",
        "## Bootstrap",
        "",
        f"- Full 2×：{payload['bootstrap']['full_2x']}",
        f"- Recent 2×：{payload['bootstrap']['recent_2x']}",
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
            "full": payload["full"],
            "recent": payload["recent"],
            "validation": payload["validation"],
            "fresh_starts": payload["fresh_starts"],
            "bootstrap": payload["bootstrap"],
            "comparison": payload["comparison"],
            "benchmarks": payload["benchmarks"],
            "formal_runs": payload["formal_runs"],
        },
        "source_result_hashes": [
            sha256(R545_RESULT),
            sha256(R543_RESULT),
            sha256(R544_RESULT),
            sha256(R542_RESULT),
        ],
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
        operation="r546_vol12_confirmation_published",
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
        raise FileExistsError("R546 results already exist; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "full_2x": payload["full"]["2x"]["stats"],
        "recent_2x": payload["recent"]["2x"]["stats"],
        "validation_2x": payload["validation"]["stats"],
        "published_result": str(published.get("result_path")),
        "ledger_record_id": published.get("ledger_record_id"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
