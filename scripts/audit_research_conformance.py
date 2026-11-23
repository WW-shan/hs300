#!/usr/bin/env python3
"""Classify research runners by project data/backtest conformance."""

from __future__ import annotations

import ast
import hashlib
import json
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
    engine = any(token in text for token in PROJECT_ENGINE_TOKENS)
    project_data = any(token in text for token in PROJECT_DATA_TOKENS)
    external_data = bool(imported & EXTERNAL_DATA_MODULES)
    formal_evidence = "run_with_evidence" in text
    research_claim = any(token in text for token in PORTFOLIO_OUTPUT_TOKENS)
    statistical = any(token.lower() in path.name.lower() for token in STATISTICAL_TOKENS)
    research_type = str((payload or {}).get("research_type") or "").lower()
    if payload:
        classification = str(payload.get("classification") or "").lower()
        statistical = statistical or any(token in classification for token in STATISTICAL_TOKENS)
    diagnostic_types = {
        "external_data_feasibility",
        "external_data_diagnostic_simulation",
        "statistical_screen",
        "attribution",
    }
    if research_type in diagnostic_types:
        status = f"diagnostic_{research_type}"
    elif research_claim and engine and project_data and formal_evidence:
        status = "project_chain_with_formal_evidence"
    elif research_claim and engine and project_data:
        status = "project_chain_without_formal_evidence"
    elif research_claim and engine and external_data:
        status = "external_data_with_project_engine"
    elif research_claim and not engine:
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
        "external_data_import": external_data,
        "return_metric_claim": research_claim,
        "research_type": research_type or None,
        "status": status,
    }


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
    research_types.update({
        "scripts/research_r520_industry_index_feasibility.py": "external_data_feasibility",
        "scripts/research_r521_industry_index_momentum.py": "external_data_diagnostic_simulation",
    })
    research_types.update({
        "scripts/research_alt_factor_ic.py": "statistical_screen",
        "scripts/research_factor_increment.py": "statistical_screen",
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
                item["status"] = f"diagnostic_{item['research_type']}"
            elif research_types.get(runner):
                item["research_type"] = research_types[runner]
                item["status"] = f"diagnostic_{research_types[runner]}"
            runners[runner] = item
    for path in sorted((ROOT / "scripts").glob("research_*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in runners:
            continue
        item = classify(path, None)
        if research_types.get(rel):
            item["research_type"] = research_types[rel]
            item["status"] = f"diagnostic_{research_types[rel]}"
        runners[rel] = item
    counts: dict[str, int] = {}
    for item in runners.values():
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    payload = {
        "schema_version": 1,
        "generated_at": "2026-11-23",
        "method": (
            "Static classification only. project_engine means the runner invokes "
            "the project's Tivelo/strategy backtest classes; project_data_access "
            "means it reads the normalized project datasets; formal evidence means "
            "run_with_evidence is present. This does not replace a live rerun."
        ),
        "counts": dict(sorted(counts.items())),
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
