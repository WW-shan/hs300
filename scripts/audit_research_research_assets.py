#!/usr/bin/env python3
"""Audit research runners, strategies, and factor-library assets for disposition."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESEARCH = DATA / "factor_research" / "research"
OUTPUT = RESEARCH / "research_asset_ledger_2026-11-24.json"
STRATEGY_DIRS = (
    DATA / "strategies" / "ai",
    DATA / "strategies" / "custom",
    ROOT / "backend" / "app" / "strategy" / "seeds",
)
FACTOR_SET_DIR = DATA / "factor_research" / "sets"
CATALOG = DATA / "factor_research" / "catalog" / "latest.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def sidecar(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def meta_of(path: Path) -> dict[str, Any] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "META"
            for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return None
            return value if isinstance(value, dict) else None
    return None


def runner_row(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    imports: set[str] = set()
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
    except SyntaxError:
        pass
    has_project_engine = any(
        token in text
        for token in ("StrategyBacktestService", "FactorBacktestService", "BacktestEngine")
    )
    has_project_data = any(
        token in text
        for token in ("DataStore(", "KlineRepository(", "load_candidate_panel(", "load_fundamental_snapshot(")
    )
    formal = "run_with_evidence" in text
    external = bool(imports & {"akshare", "requests", "httpx", "urllib3", "aiohttp"})
    if formal and has_project_engine and has_project_data:
        status = "project_formal"
    elif has_project_engine and has_project_data:
        status = "project_engine_no_formal_evidence"
    elif has_project_engine and external:
        status = "external_data_with_project_engine"
    elif external:
        status = "external_or_custom_probe"
    else:
        status = "statistical_or_derived"
    return {
        "kind": "runner",
        "path": rel(path),
        "sha256": sha(path),
        "status": status,
        "project_engine": has_project_engine,
        "project_data": has_project_data,
        "formal_evidence": formal,
        "external_data": external,
        "disposition": (
            "keep" if status in {"project_formal", "statistical_or_derived"} else
            "convert_or_quarantine"
        ),
    }


def strategy_row(path: Path) -> dict[str, Any]:
    if path.parent == DATA / "strategies" / "custom":
        meta = meta_of(path)
        return {
            "kind": "strategy",
            "path": rel(path),
            "sha256": sha(path),
            "status": "private_runtime",
            "disposition": "keep_private",
            "research_only": meta.get("research_only") if meta else None,
            "execution_backend": None,
            "params": len((meta or {}).get("params") or []),
            "scoring": len((meta or {}).get("scoring") or []),
            "rule_count": None,
            "missing_meta_keys": [],
        }
    meta = meta_of(path)
    if meta is None:
        return {
            "kind": "strategy",
            "path": rel(path),
            "sha256": sha(path),
            "status": "invalid_meta",
            "disposition": "delete_or_quarantine",
        }
    text = path.read_text(encoding="utf-8")
    missing = [
        key for key in (
            "id", "name", "description", "tags", "asset_types", "timeframes",
            "params", "scoring", "order_by", "descending", "limit",
        )
        if key not in meta
    ]
    rules = re.search(r'RULES\s*=\s*("""(?:.|\n)*?"""|\'(?:.|\n)*?\')', text)
    rule_count = 0
    if rules:
        try:
            rule_text = ast.literal_eval(rules.group(1))
            rule_count = len(re.findall(r"(?m)^\s*\d+\.", rule_text))
        except (ValueError, SyntaxError):
            rule_count = 0
    # Seeds are the upgrade-compat source for old built-in strategies; they
    # are not public research strategies, so research_only is not required.
    research_ok = (
        path.parent.name == "seeds"
        or meta.get("research_only") is True
        or path.parent.parent.name == "seeds"
    )
    return {
        "kind": "strategy",
        "path": rel(path),
        "sha256": sha(path),
        "status": "valid" if not missing and research_ok else "noncompliant",
        "disposition": "keep" if not missing and research_ok else "convert_or_delete",
        "research_only": meta.get("research_only"),
        "execution_backend": re.search(r'EXECUTION_BACKEND\s*=\s*"([^"]+)"', text).group(1)
        if re.search(r'EXECUTION_BACKEND\s*=\s*"([^"]+)"', text)
        else None,
        "params": len(meta.get("params") or []),
        "scoring": len(meta.get("scoring") or {}),
        "rule_count": rule_count,
        "missing_meta_keys": missing,
    }


def factor_row(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "kind": "factor_definition",
            "path": rel(path),
            "sha256": sha(path),
            "status": "invalid_json",
            "disposition": "delete_or_quarantine",
        }
    return {
        "kind": "factor_definition",
        "path": rel(path),
        "sha256": sha(path),
        "status": "valid",
        "factor_id": payload.get("id"),
        "factor_kind": payload.get("kind"),
        "factor_status": payload.get("status"),
        "disposition": "keep",
    }


def main() -> int:
    # Idempotent audit: rebuild in memory, then publish only when the immutable
    # payload is unchanged or the output does not yet exist.  Never rewrite a
    # historical ledger with different content.
    existing: bytes | None = OUTPUT.read_bytes() if OUTPUT.exists() else None
    rows: list[dict[str, Any]] = []
    rows.extend(runner_row(path) for path in sorted((ROOT / "scripts").glob("research_*.py")))
    for directory in STRATEGY_DIRS:
        if directory.exists():
            rows.extend(strategy_row(path) for path in sorted(directory.glob("*.py")))
    for directory in (DATA / "user_data/custom_factors", DATA / "factor_research/candidates"):
        if directory.exists():
            rows.extend(factor_row(path) for path in sorted(directory.rglob("*.json")))
    counts: dict[str, int] = {}
    dispositions: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        dispositions[row["disposition"]] = dispositions.get(row["disposition"], 0) + 1
    payload = {
        "schema_version": 1,
        "generated_at": "2026-11-24",
        "method": "Static inventory plus AST metadata checks. A status is a disposition input, not a claim of live backtest success.",
        "counts": dict(sorted(counts.items())),
        "dispositions": dict(sorted(dispositions.items())),
        "rows": rows,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    if existing is not None and existing != raw:
        raise RuntimeError("research asset ledger content changed; publish a new dated version")
    if existing is None:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(raw)
    sidecar(OUTPUT)
    print(json.dumps({"output": rel(OUTPUT), "counts": counts, "dispositions": dispositions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
