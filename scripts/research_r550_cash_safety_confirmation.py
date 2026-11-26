#!/usr/bin/env python3
"""R550: confirm the fixed cash-quality + safety add-on variant."""

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.evidence_guard import require_formal_evidence  # noqa: E402
from app.factor_research.research_contract import (  # noqa: E402
    ResearchResultEnvelope,
    publish_research_artifacts,
)

RESEARCH_TYPE = "project_formal"
RESEARCH_ID = "R550"
RESEARCH_PREREGISTRATION = (
    "data/factor_research/research/r550_cash_safety_confirmation_preregistration.json"
)
RUNNER = "scripts/research_r550_cash_safety_confirmation.py"
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
R549_RESULT = RESEARCH / "r549b_quality_addon_portfolio_result.json"
R549_PREREG = RESEARCH / "r549b_quality_addon_portfolio_preregistration.json"
R549_RUNNER = ROOT / "scripts" / "research_r549_quality_addon_portfolio.py"
R548_RESULT = RESEARCH / "r548_multi_horizon_incremental_screen_result.json"
R548_PREREG = RESEARCH / "r548_multi_horizon_incremental_screen_preregistration.json"
R548_RUNNER = ROOT / "scripts" / "research_r548_multi_horizon_incremental_screen.py"
R547_RESULT = RESEARCH / "r547_incremental_factor_screen_result.json"
R547_PREREG = RESEARCH / "r547_incremental_factor_screen_preregistration.json"
R547_RUNNER = ROOT / "scripts" / "research_r547_incremental_factor_screen.py"
R546_RESULT = RESEARCH / "r546_vol12_confirmation_result.json"
PREREG = RESEARCH / "r550_cash_safety_confirmation_preregistration.json"
RESULT = RESEARCH / "r550_cash_safety_confirmation_result.json"
MARKDOWN = RESEARCH / "r550_cash_safety_confirmation_result.md"

FIXED_VARIANT = "cash_safety"
FIXED_ADDONS = (
    "cf_r547_cash_quality",
    "cf_r547_value_quality_safety",
)
START = date(2022, 5, 5)
TRAIN_END = date(2024, 12, 31)
VALIDATION_START = date(2025, 1, 1)
VALIDATION_END = date(2025, 10, 13)
RECENT_START = date(2025, 10, 14)
END = date(2026, 11, 5)
FRESH_STARTS = (
    date(2022, 5, 5),
    date(2023, 1, 3),
    date(2024, 1, 2),
    date(2025, 1, 2),
    date(2025, 10, 14),
    date(2026, 1, 5),
)
FORMAL_RUN_COUNT = 13


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


def policy_hash(value: dict[str, str]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def execution_contract() -> dict[str, Any]:
    return {
        "matching": "open_t+1",
        "cost_multiplier": 2,
        "leverage": "none",
        "max_exposure_pct": 1.0,
    }


def scientific_design() -> dict[str, Any]:
    return {
        "fixed_variant": FIXED_VARIANT,
        "fixed_addons": list(FIXED_ADDONS),
        "selection_basis": "post_hoc_fixed_from_r549_discovery",
        "recent_is_independent_oos": False,
        "formal_runs": FORMAL_RUN_COUNT,
        "execution": {
            **execution_contract(),
            "max_positions": 10,
            "max_hold_days": 60,
            "industry_cap": 3,
            "stop_loss": -0.12,
            "trailing_stop": None,
            "five_x_policy": "warning_only",
        },
        "windows": {
            "start": str(START),
            "train_end": str(TRAIN_END),
            "validation_start": str(VALIDATION_START),
            "validation_end": str(VALIDATION_END),
            "recent_start": str(RECENT_START),
            "end": str(END),
            "fresh_starts": [str(value) for value in FRESH_STARTS],
        },
        "gates": {
            "full_annual_min": 0.10,
            "full_sharpe_min": 0.90,
            "full_mdd_min": -0.18,
            "recent_return_positive": True,
            "validation_annual_positive": True,
            "fresh_positive_min": 4,
            "fresh_median_positive": True,
            "stress_3x_annual_min": 0.08,
            "stress_3x_mdd_min": -0.20,
            "stress_3x_recent_min": -0.03,
            "five_x_hard_rejection": False,
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
        "membership": root / RESEARCH / "r526_unified_pit_bundle_window" / "daily_snapshots.parquet",
        "industry": root / RESEARCH / "r526_unified_pit_bundle_window" / "industry_history.parquet",
        "metrics": root / "data" / "financials" / "metrics" / "part.parquet",
        "income": root / "data" / "financials" / "income" / "part.parquet",
        "balance_sheet": root / "data" / "financials" / "balance_sheet" / "part.parquet",
        "cash_flow": root / "data" / "financials" / "cash_flow" / "part.parquet",
        "alt_config": root / "data" / "ext_data" / "alt_hs300" / "config.json",
        "r546_result": root / R546_RESULT.relative_to(ROOT),
        "r547_result": root / R547_RESULT.relative_to(ROOT),
        "r547_preregistration": root / R547_PREREG.relative_to(ROOT),
        "r547_runner": root / R547_RUNNER.relative_to(ROOT),
        "r548_result": root / R548_RESULT.relative_to(ROOT),
        "r548_preregistration": root / R548_PREREG.relative_to(ROOT),
        "r548_runner": root / R548_RUNNER.relative_to(ROOT),
        "r549_result": root / R549_RESULT.relative_to(ROOT),
        "r549_preregistration": root / R549_PREREG.relative_to(ROOT),
        "r549_runner": root / R549_RUNNER.relative_to(ROOT),
        "alt_timeseries": root / "data" / "ext_data" / "alt_hs300" / "timeseries",
    }


def freeze_preregistration(root: Path) -> Path:
    root = Path(root)
    if PREREG.exists() or PREREG.with_name(PREREG.name + ".sha256").exists():
        raise FileExistsError(f"R550 preregistration already exists: {PREREG}")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    input_hashes = {
        name: sha256(path)
        for name, path in _input_paths(root).items()
        if name != "alt_timeseries"
    }
    input_hashes["alt_timeseries"] = r547._ext_timeseries_hash()
    input_hashes["candidate_definitions"] = policy_hash(candidate_hashes)
    payload = {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "question": (
            "Does the R549 post-hoc cash-quality plus value-quality-safety variant "
            "confirm on the full project portfolio, including recent and fresh starts, "
            "without leverage or three-times-cost failure?"
        ),
        "hypothesis": (
            "The fixed cash_safety variant can preserve absolute full-period return, "
            "reduce drawdown, and turn recent-year return positive, but its post-hoc "
            "selection means confirmation is watch-only."
        ),
        "study_scope": "research_only",
        "runner": RUNNER,
        "runner_sha256": sha256(Path(__file__).resolve()),
        "data_source": "project",
        "input_hashes": input_hashes,
        "parameters": {
            **scientific_design(),
            "candidate_definitions": candidate_hashes,
        },
        "sample": scientific_design()["windows"],
        "execution": execution_contract(),
        "gates": scientific_design()["gates"],
        "falsification_checks": [
            "fixed_variant_no_tuning",
            "recent_is_not_independent_oos",
            "formal_evidence_saved_lookahead_t1",
            "financial_factors_use_announcement_t_plus_one_gate",
            "r526_pit_membership_and_industry_used",
            "five_x_warning_only",
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
        raise RuntimeError("R550 runner hash no longer matches preregistration")
    r547 = _load_module("research_r547_incremental_factor_screen")
    definitions = r547.load_candidate_definitions(root)
    candidate_hashes = {
        factor_id: sha256(
            root
            / "data"
            / "factor_research"
            / "candidates"
            / "r547"
            / f"{factor_id}.json"
        )
        for factor_id in definitions
    }
    if prereg["parameters"]["candidate_definitions"] != candidate_hashes:
        raise RuntimeError("R550 candidate definition hash changed")
    if prereg["input_hashes"]["candidate_definitions"] != policy_hash(candidate_hashes):
        raise RuntimeError("R550 candidate definition aggregate hash changed")
    for name, path in _input_paths(root).items():
        if name == "alt_timeseries":
            continue
        if sha256(path) != prereg["input_hashes"].get(name):
            raise RuntimeError(f"R550 input changed: {name}")
    if r547._ext_timeseries_hash() != prereg["input_hashes"].get("alt_timeseries"):
        raise RuntimeError("R550 extension timeseries changed")


def _run_window(
    p5: Any,
    service: Any,
    runtime: dict[str, Any],
    panel: pl.DataFrame,
    *,
    label: str,
    start: date,
    end: date,
    cost_label: str,
) -> dict[str, Any]:
    r545 = _load_module("research_r545_stable7_risk_budget")
    r546 = _load_module("research_r546_vol12_confirmation")
    prepared = r545.prepare_panel(
        p5,
        service,
        runtime,
        panel,
        start=start,
        end=end,
    )
    config = runtime["r533"].build_formal_config(
        p5,
        prepared,
        start=start,
        end=end,
        max_positions=10,
        max_hold_days=60,
        cost_label=cost_label,
        score_field="score",
    )
    config.overrides = {
        **(config.overrides or {}),
        "stop_loss": -0.12,
        "trailing_stop": None,
    }
    config.params = {
        **(config.params or {}),
        "industry_by_symbol": runtime["industries"],
        "max_industry_positions": 3,
    }
    config.max_exposure_pct = 1.0
    config.position_sizing = "policy"
    config.position_policy = r546.VARIANT["id"]
    config.position_policy_params = {}
    if prepared.signature != service._matrix_prepare_signature(config):
        raise RuntimeError(f"R550:{label}: prepared signature mismatch")
    with r545.policy_injection(r546.VARIANT):
        result = service.run_with_evidence(config, prepared=prepared)
        evidence = require_formal_evidence(result, label=f"R550:{label}")
        if result.error:
            raise RuntimeError(f"R550:{label}: {result.error}")
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


def run_study(root: Path) -> dict[str, Any]:
    _verify_binding(root)
    r542 = _load_module("research_r542_recent_failure_attribution")
    r547 = _load_module("research_r547_incremental_factor_screen")
    r549 = _load_module("research_r549_quality_addon_portfolio")
    runtime = r542.load_runtime(root)
    p5 = runtime["p5"]
    service = p5.portfolio._service()
    membership = r547._load_membership(root)
    candidate_panel = r547._load_price_panel(root, membership)
    candidate_panel = r547._add_candidate_sources(
        candidate_panel,
        r547.load_candidate_definitions(root),
    )
    panel = r549.build_variant_panel(runtime, candidate_panel, FIXED_ADDONS)
    full = {}
    recent = {}
    for cost in ("2x", "3x", "5x"):
        full[cost] = _run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"cash_safety_full_{cost}",
            start=START,
            end=END,
            cost_label=cost,
        )
        recent[cost] = _run_window(
            p5,
            service,
            runtime,
            panel,
            label=f"cash_safety_recent_{cost}",
            start=RECENT_START,
            end=END,
            cost_label=cost,
        )
    validation = _run_window(
        p5,
        service,
        runtime,
        panel,
        label="cash_safety_validation_2x",
        start=VALIDATION_START,
        end=VALIDATION_END,
        cost_label="2x",
    )
    fresh = []
    for start in FRESH_STARTS:
        fresh.append(
            _run_window(
                p5,
                service,
                runtime,
                panel,
                label=f"cash_safety_fresh_{start.isoformat()}_2x",
                start=start,
                end=END,
                cost_label="2x",
            )
        )
    all_runs = [*full.values(), *recent.values(), validation, *fresh]
    if len(all_runs) != FORMAL_RUN_COUNT:
        raise RuntimeError(f"R550 expected {FORMAL_RUN_COUNT} formal runs, got {len(all_runs)}")
    evidence_ok = all(
        run["formal_evidence"]["persistence"] == "saved"
        and run["formal_evidence"]["lookahead"] == "passed"
        and run["formal_evidence"]["execution_timing"] == "passed"
        for run in all_runs
    )
    full_2x = full["2x"]
    recent_2x = recent["2x"]
    full_2x["bootstrap"] = runtime["r533"].bootstrap_curve(full_2x["curve"], seed=55001)
    recent_2x["bootstrap"] = runtime["r533"].bootstrap_curve(recent_2x["curve"], seed=55002)
    gate = r549.final_gate(
        full=full_2x["stats"],
        recent=recent_2x["stats"],
        validation=validation["stats"],
        fresh_values=[float(run["stats"]["total_return"]) for run in fresh],
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
    previous = json.loads(R549_RESULT.read_text(encoding="utf-8"))
    previous_cash_safety = previous["metrics"]["discovery"][FIXED_VARIANT]
    r546 = json.loads(R546_RESULT.read_text(encoding="utf-8"))
    comparison = {
        "r549_discovery": previous_cash_safety,
        "r546_full_2x": r546["metrics"]["full"]["2x"],
        "r546_recent_2x": r546["metrics"]["recent"]["2x"],
        "full_annual_delta_vs_r546": round(
            float(full_2x["stats"]["annual_return"])
            - float(r546["metrics"]["full"]["2x"]["stats"]["annual_return"]),
            8,
        ),
        "full_mdd_delta_vs_r546": round(
            float(full_2x["stats"]["max_drawdown"])
            - float(r546["metrics"]["recent"]["2x"]["stats"]["max_drawdown"]),
            8,
        ),
        "recent_return_delta_vs_r546": round(
            float(recent_2x["stats"]["total_return"])
            - float(r546["metrics"]["recent"]["2x"]["stats"]["total_return"]),
            8,
        ),
    }
    return {
        "schema_version": 1,
        "research_id": RESEARCH_ID,
        "research_type": RESEARCH_TYPE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "design": scientific_design(),
        "selected_variant": FIXED_VARIANT,
        "selected_addons": list(FIXED_ADDONS),
        "full": {cost: _public_run(run) for cost, run in full.items()},
        "recent": {cost: _public_run(run) for cost, run in recent.items()},
        "validation": _public_run(validation),
        "fresh_starts": [_public_run(run) for run in fresh],
        "bootstrap": {
            "full_2x": full_2x["bootstrap"],
            "recent_2x": recent_2x["bootstrap"],
        },
        "comparison": comparison,
        "gate": gate,
        "formal_runs": [_public_run(run) for run in all_runs],
        "conclusion": gate["conclusion"],
        "known_limits": [
            "The cash_safety variant was nominated after R549 discovery, so this is a post-hoc confirmation and not independent OOS.",
            "R546 and earlier research already observed recent-year returns.",
            "The two add-on composites are fixed draft definitions with no weight search.",
            "Five-times cost is warning-only and no leverage is used.",
            "No active version, orders, paper targets or production enablement are produced.",
        ],
        "negative_knowledge": [] if gate["conclusion"] == "watch_only" else gate["failed_gates"],
        "output_policy": scientific_design()["output_policy"],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# R550 cash_safety 固定变体确认",
        "",
        "> 这是 R549 发现后的事后固定确认，不是独立样本；结论最多 `watch_only`。",
        "",
        "## 结果",
        "",
        "| 窗口/成本 | 年化 | Sharpe | MDD | 总收益 | 交易数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, run in (
        ("full 2x", payload["full"]["2x"]),
        ("full 3x", payload["full"]["3x"]),
        ("full 5x", payload["full"]["5x"]),
        ("recent 2x", payload["recent"]["2x"]),
        ("recent 3x", payload["recent"]["3x"]),
        ("recent 5x", payload["recent"]["5x"]),
        ("validation 2x", payload["validation"]),
    ):
        stats = run["stats"]
        lines.append(
            f"| {label} | {stats['annual_return']:.2%} | {stats['sharpe']:.2f} "
            f"| {stats['max_drawdown']:.2%} | {stats['total_return']:.2%} "
            f"| {int(stats['n_trades'])} |"
        )
    lines += [
        "",
        "## 与 R546 对比",
        "",
        f"- Full 年化差：{payload['comparison']['full_annual_delta_vs_r546']:+.2%}",
        f"- Recent 收益差：{payload['comparison']['recent_return_delta_vs_r546']:+.2%}",
        "",
        "## Fresh starts",
        "",
        "| 起始日 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for run in payload["fresh_starts"]:
        stats = run["stats"]
        lines.append(
            f"| {run['start']} | {stats['total_return']:.2%} | {stats['annual_return']:.2%} "
            f"| {stats['sharpe']:.2f} | {stats['max_drawdown']:.2%} |"
        )
    lines += [
        "",
        "## 门槛",
        "",
        f"- 核心通过：{payload['gate']['core_pass']}",
        f"- 3× 压力通过：{payload['gate']['stress_pass']}",
        f"- Fresh-start：{payload['gate']['fresh_start']}",
        f"- 5× warnings：{payload['gate']['five_x_warnings']}",
        f"- 失败项：{payload['gate']['failed_gates']}",
        f"- 结论：`{payload['conclusion']}`",
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
    result = ResearchResultEnvelope.model_validate(
        {
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
                "selected_variant": payload["selected_variant"],
                "selected_addons": payload["selected_addons"],
                "full": payload["full"],
                "recent": payload["recent"],
                "validation": payload["validation"],
                "fresh_starts": payload["fresh_starts"],
                "bootstrap": payload["bootstrap"],
                "comparison": payload["comparison"],
                "formal_runs": payload["formal_runs"],
            },
            "source_result_hashes": [
                sha256(R549_RESULT),
                sha256(R548_RESULT),
                sha256(R546_RESULT),
            ],
            "formal_evidence": payload["full"]["2x"]["formal_evidence"],
            "known_limits": payload["known_limits"],
            "negative_knowledge": payload["negative_knowledge"],
            "output_policy": payload["output_policy"],
        }
    )
    return publish_research_artifacts(
        DATA,
        preregistration_path=PREREG,
        result=result,
        markdown=render_markdown(payload),
        operation="r550_cash_safety_confirmation_published",
    ).__dict__


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
        raise FileExistsError("R550 result already exists; refusing overwrite")
    payload = run_study(ROOT)
    published = publish_result(payload)
    print(
        json.dumps(
            {
                "conclusion": payload["conclusion"],
                "full_2x": payload["full"]["2x"]["stats"],
                "recent_2x": payload["recent"]["2x"]["stats"],
                "validation_2x": payload["validation"]["stats"],
                "gate": payload["gate"],
                "published_result": str(published.get("result_path")),
                "ledger_record_id": published.get("ledger_record_id"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
