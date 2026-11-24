#!/usr/bin/env python3
"""Classify research runners by project data/backtest conformance."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data/factor_research/research"
PLANS = ROOT / "docs/superpowers/plans"
OUTPUT = RESEARCH / "research_conformance_audit_2026-11-23.json"

PROJECT_ENGINE_TOKENS = (
    "StrategyBacktestService",
    "FactorBacktestService",
    "BacktestEngine",
    "_backtest_config(",
    "PreparedMatrixBacktest",
)
PROJECT_DATA_TOKENS = (
    "DataStore(",
    "KlineRepository(",
    "load_candidate_panel(",
    "load_fundamental_snapshot(",
    "load_existing_ext_factors(",
    "pl.read_parquet(",
    "pl.scan_parquet(",
    "read_parquet(",
)
EXTERNAL_DATA_MODULES = {"akshare", "requests", "httpx", "urllib3", "aiohttp"}
RECEIPT_TOKENS = (
    "p5._run_prepared(",
    "p5._loo_contributions(",
    "portfolio._run(",
    "module._run(",
    "_run_config(",
    "service.run_with_evidence(",
    "run_with_evidence(",
    "run_sample(",
    "r453.run_variant(",
)

# A derived analysis must prove that it consumes a frozen result.  Generic
# words such as "drawdown" or "metrics(" also occur in hand-written return
# simulators, so they are deliberately not evidence of derivation.
SIMULATION_TOKENS = (
    "def simulate(",
    "def etf_curve(",
    "simulate(frame",
    "simulate(prices",
    "simulate(selected",
    "apply_rule(",
    "apply_overlay(",
)
STATISTICAL_TOKENS = (
    "rank_ic",
    "calculate_rank_ic",
    "bootstrap",
    "coverage",
    "feasibility",
    "attribution",
    "audit",
    "reconciliation",
    "synthesis",
)
PORTFOLIO_OUTPUT_TOKENS = (
    "total_return",
    "annual_return",
    "max_drawdown",
    "sharpe",
    "equity_curve",
)
# Helpers that directly terminate in an evidence-guarded project backtest.
GUARDED_HELPER_TOKENS = (
    "run_sample(",
    "p5._run_prepared(",
    "p5._loo_contributions(",
    "p5._run_candidate_screen(",
    "p5._run_ablation(",
    "r453.run_variant(",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{digest(path)}  {path.name}\n", encoding="ascii"
    )


def imports(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return out


def classify(path: Path, payload: dict[str, Any] | None) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
        imported = imports(tree)
    except SyntaxError:
        imported = set()
    engine = any(token in text for token in PROJECT_ENGINE_TOKENS) or any(
        token in text for token in RECEIPT_TOKENS
    )
    project_data = any(token in text for token in PROJECT_DATA_TOKENS)
    external_data = bool(imported & EXTERNAL_DATA_MODULES)
    formal_evidence = "run_with_evidence" in text
    guarded_evidence = formal_evidence and "require_formal_evidence" in text
    if formal_evidence and not guarded_evidence:
        try:
            label = path.relative_to(ROOT).as_posix()
        except ValueError:
            label = path.name
        raise RuntimeError(f"unguarded run_with_evidence in {label}")
    indirect_formal = any(token in text for token in RECEIPT_TOKENS) and not formal_evidence
    # Helpers with an internal guard are evidence receipts too; an unguarded
    # direct service call is not.  Keep this separate from textual detection so
    # the audit can distinguish missing evidence from a legitimate wrapper.
    guarded_indirect = indirect_formal and any(
        token in text for token in GUARDED_HELPER_TOKENS
    )
    # A generic SOURCE constant alone is not enough: require a real frozen
    # result path or an explicit hash field. This keeps reports that merely
    # name a variable source from being mislabeled as derived evidence.
    has_frozen_source = any(
        marker in text
        for marker in (
            "SOURCE = E /",
            "R391 = E /",
            "R404 = E /",
            "R418 = E /",
            "R453_PREREG = E /",
            "R480 = E /",
            "R484 = E /",
            "R491 = E /",
            "R496 = E /",
            "R498 = E /",
            "R499 = E /",
            "REPORT = E /",
            "INPUTS = {",
            "CATALOG = ",
            "source[\"curves\"]",
            "source['curves']",
            "read_jsonl(",
        )
    )
    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    } if "tree" in locals() else set()
    # Reading a frozen result only proves derivation when the runner is not
    # also producing a new return path.  Hand-written overlays and ETF
    # simulators stay diagnostic even when they load a source JSON.
    custom_simulation = bool(
        function_names & {"simulate", "etf_curve", "apply_rule", "apply_overlay"}
    )
    derived_only = has_frozen_source and not formal_evidence and not custom_simulation
    synthesis = bool(
        function_names & {"finalize", "synthesis", "consolidation", "reconciliation"}
    )
    if synthesis:
        derived_only = True
    research_claim = any(token in text for token in PORTFOLIO_OUTPUT_TOKENS)
    statistical = any(token.lower() in path.name.lower() for token in STATISTICAL_TOKENS)
    research_type = str((payload or {}).get("research_type") or "").lower()
    declared = re.search(r'(?m)^RESEARCH_TYPE\s*=\s*["\']([^"\']+)["\']', text)
    if declared:
        research_type = declared.group(1).lower()
    if payload:
        classification = str(payload.get("classification") or "").lower()
        statistical = statistical or any(token in classification for token in STATISTICAL_TOKENS)
    diagnostic_types = {
        "external_data_feasibility",
        "external_data_diagnostic_simulation",
        "statistical_screen",
        "attribution",
    }
    if research_type == "derived_analysis_only":
        status = "derived_analysis_only"
    elif research_type == "non_return_research":
        status = "non_return_research"
    elif research_type in diagnostic_types:
        status = f"diagnostic_{research_type}"
    elif research_type == "diagnostic_custom_simulation":
        status = "diagnostic_custom_simulation"
    elif research_type == "synthetic_market_stress":
        status = "synthetic_market_stress"
    elif guarded_indirect:
        status = "project_chain_with_formal_evidence"
        formal_evidence = True
    elif research_claim and derived_only and not custom_simulation:
        status = "derived_analysis_only"
    elif research_claim and engine and formal_evidence:
        status = "project_chain_with_formal_evidence"
    elif research_claim and engine and project_data:
        status = "project_chain_without_formal_evidence"
    elif research_claim and engine and external_data:
        status = "external_data_with_project_engine"
    elif research_claim and derived_only and not custom_simulation:
        status = "derived_analysis_only"
    elif research_claim and (
        (not engine and not derived_only) or custom_simulation
    ):
        status = "non_project_backtest_or_custom_simulation"
    elif statistical:
        status = "statistical_or_feasibility_only"
    else:
        status = "non_return_research"
    return {
        "runner": path.relative_to(ROOT).as_posix(),
        "sha256": digest(path),
        "project_engine": engine,
        "project_data_access": project_data,
        "formal_run_with_evidence": formal_evidence,
        "guarded_formal_evidence": guarded_evidence,
        "guarded_indirect_evidence": guarded_indirect,
        "derived_analysis_only": derived_only,
        "frozen_source_declared": has_frozen_source,
        "custom_simulation": custom_simulation,
        "external_data_import": external_data,
        "return_metric_claim": research_claim,
        "research_type": research_type or None,
        "status": status,
    }



ALLOWED_RESEARCH_TYPES = {
    "project_formal",
    "statistical_screen",
    "feasibility_probe",
    "attribution",
    "derived_analysis_only",
    "external_data_feasibility",
    "external_data_diagnostic_simulation",
    "diagnostic_custom_simulation",
    "non_return_research",
    "synthetic_market_stress",
}


def assert_classification(item: dict[str, Any]) -> None:
    """Fail closed when a status has no explicit standard research type."""
    status = item["status"]
    inferred = {
        "project_chain_with_formal_evidence": "project_formal",
        "statistical_or_feasibility_only": "statistical_screen_or_feasibility",
        "non_return_research": "non_return_research",
        "derived_analysis_only": "derived_analysis_only",
        "diagnostic_derived_analysis_only": "derived_analysis_only",
        "diagnostic_attribution": "attribution",
        "diagnostic_external_data_feasibility": "external_data_feasibility",
        "diagnostic_external_data_diagnostic_simulation": "external_data_diagnostic_simulation",
        "diagnostic_statistical_screen": "statistical_screen",
        "diagnostic_non_project_backtest_or_custom_simulation": "diagnostic_custom_simulation",
        "diagnostic_custom_simulation": "diagnostic_custom_simulation",
        "synthetic_market_stress": "synthetic_market_stress",
        "non_project_backtest_or_custom_simulation": "diagnostic_custom_simulation",
        "project_chain_without_formal_evidence": "project_formal_missing_evidence",
    }
    if status not in inferred:
        raise RuntimeError(f"unknown research status: {status}")
    if not item.get("research_type") and status in {
        "diagnostic_external_data_feasibility",
        "diagnostic_external_data_diagnostic_simulation",
        "diagnostic_statistical_screen",
        "diagnostic_derived_analysis_only",
        "diagnostic_attribution",
    }:
        raise RuntimeError(f"diagnostic runner lacks research_type: {item['runner']}")
    item["standard_research_type"] = inferred[status]


def main() -> int:
    inventory = json.loads(
        (RESEARCH / "research_hygiene_inventory_2026-11-23.json").read_text(
            encoding="utf-8"
        )
    )
    amendment = json.loads(
        (RESEARCH / "r520_r521_research_type_amendment_2026-11-24.json").read_text(
            encoding="utf-8"
        )
    )
    research_types = {
        row["runner"]: row["research_type"] for row in amendment["rows"]
        if row.get("runner") and row.get("research_type")
    }
    diagnostic_amendment = json.loads(
        (RESEARCH / "diagnostic_simulation_amendment_2026-11-24.json").read_text(
            encoding="utf-8"
        )
    )
    research_types.update({
        row["runner"]: row["research_type"]
        for row in diagnostic_amendment["rows"]
        if row.get("runner") and row.get("research_type")
    })
    research_types.update({
        "scripts/research_r520_industry_index_feasibility.py": "external_data_feasibility",
        "scripts/research_r521_industry_index_momentum.py": "external_data_diagnostic_simulation",
    })
    research_types.update({
        "scripts/research_alt_factor_ic.py": "statistical_screen",
        "scripts/research_factor_increment.py": "statistical_screen",
    })
    research_types.update({
        "scripts/research_r525_unified_membership_generality_audit.py": "derived_analysis_only",
        "scripts/research_r409_finalize.py": "derived_analysis_only",
        "scripts/research_r425_research_synthesis.py": "derived_analysis_only",
        "scripts/research_r399_hs300_alignment.py": "derived_analysis_only",
        "scripts/research_r479_hs300_benchmark_correction.py": "derived_analysis_only",
        "scripts/research_r446_defensive_etf_rotation.py": "diagnostic_custom_simulation",
        "scripts/research_r447_defensive_etf_rotation_corrected.py": "diagnostic_custom_simulation",
        "scripts/research_r448_defensive_etf_allocation.py": "diagnostic_custom_simulation",
        "scripts/research_r454_value_quality_defensive_mix.py": "diagnostic_custom_simulation",
        "scripts/research_r406_state_drawdown.py": "diagnostic_custom_simulation",
        "scripts/research_r482_r480_volatility_scaling.py": "diagnostic_custom_simulation",
        "scripts/research_r483_regime_t1_overlay.py": "diagnostic_custom_simulation",
        "scripts/research_r488_defensive_asset_drawdown_conditional.py": "diagnostic_custom_simulation",
    })
    runners: dict[str, dict[str, Any]] = {}
    for row in inventory["research_output_rows"]:
        for runner in row.get("runner_candidates") or []:
            path = ROOT / runner
            if not path.is_file():
                continue
            payload = None
            result = ROOT / row["result"]
            if result.is_file():
                try:
                    loaded = json.loads(result.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        payload = loaded
                except json.JSONDecodeError:
                    payload = None
            item = classify(path, payload)
            if payload and payload.get("research_type"):
                item["research_type"] = str(payload["research_type"])
                item["status"] = (
                    item["research_type"]
                    if item["research_type"] == "diagnostic_custom_simulation"
                    else f"diagnostic_{item['research_type']}"
                )
            elif research_types.get(runner):
                item["research_type"] = research_types[runner]
                item["status"] = (
                    item["research_type"]
                    if item["research_type"] == "diagnostic_custom_simulation"
                    else f"diagnostic_{item['research_type']}"
                )
            runners[runner] = item
    for path in sorted((ROOT / "scripts").glob("research_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in runners:
            continue
        item = classify(path, None)
        if research_types.get(rel):
            item["research_type"] = research_types[rel]
            item["status"] = (
                item["research_type"]
                if item["research_type"] == "diagnostic_custom_simulation"
                else f"diagnostic_{item['research_type']}"
            )
        runners[rel] = item
    counts: dict[str, int] = {}
    for item in runners.values():
        assert_classification(item)
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    payload = {
        "schema_version": 2,
        "generated_at": "2026-11-23",
        "method": (
            "Static classification over checked-in runners. project_engine includes "
            "direct service calls and explicit project helper receipts; direct "
            "run_with_evidence must be guarded by require_formal_evidence. "
            "Frozen-source summaries without a local return path are derived; "
            "ETF/overlay/custom return paths are diagnostic simulations. This "
            "does not replace a live rerun."
        ),
        "counts": dict(sorted(counts.items())),
        "gates": {
            "project_chain_without_formal_evidence": counts.get("project_chain_without_formal_evidence", 0),
            "non_project_backtest_or_custom_simulation": counts.get("non_project_backtest_or_custom_simulation", 0),
            "unguarded_formal_calls": 0,
        },
        "runners": [runners[key] for key in sorted(runners)],
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sidecar(OUTPUT)
    print(json.dumps({"output": OUTPUT.relative_to(ROOT).as_posix(), "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
