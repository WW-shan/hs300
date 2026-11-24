#!/usr/bin/env python3
"""Audit mandatory research-contract conformance for repository runners."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.factor_research.research_contract import (  # noqa: E402
    ResearchPreregistration,
    ResearchResultEnvelope,
    validate_result_for_preregistration,
)

RESEARCH = ROOT / "data/factor_research/research"
GRANDFATHER = RESEARCH / "research_contract_grandfather_2026-11-24.json"
OUTPUT = RESEARCH / "research_contract_audit_2026-11-24.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sidecar(path: Path) -> None:
    path.with_name(path.name + ".sha256").write_text(
        f"{digest(path)}  {path.name}\n", encoding="ascii"
    )


def runner_paths(root: Path) -> list[Path]:
    return sorted((root / "scripts").glob("research_*.py")) + sorted(
        (root / "data/strategies/ai/.iterations").rglob("evidence/run_*.py")
    )


def top_level_assignments(path: Path) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            result[target.id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
    return result


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _load_prereg(root: Path, relative: str, runner_sha: str) -> tuple[ResearchPreregistration | None, str | None]:
    path = root / relative
    if not path.is_file():
        return None, f"preregistration_missing:{relative}"
    side = path.with_name(path.name + ".sha256")
    if not side.is_file() or side.read_text(encoding="ascii").split()[0] != digest(path):
        return None, f"preregistration_sidecar_invalid:{relative}"
    try:
        payload = ResearchPreregistration.model_validate_json(path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return None, f"preregistration_invalid:{relative}:{exc}"
    if payload.runner_sha256 != runner_sha:
        return None, f"preregistration_runner_hash_mismatch:{relative}"
    return payload, None


def _result_errors(
    root: Path, prereg: ResearchPreregistration, prereg_path: Path
) -> list[str]:
    stem = prereg_path.name.removesuffix("_preregistration.json")
    result_path = prereg_path.with_name(stem + "_result.json")
    markdown_path = prereg_path.with_name(stem + "_result.md")
    errors: list[str] = []
    for path in (result_path, markdown_path):
        side = path.with_name(path.name + ".sha256")
        if not path.is_file() or not side.is_file():
            errors.append(f"result_artifact_missing:{_relative(root, path)}")
        elif side.read_text(encoding="ascii").split()[0] != digest(path):
            errors.append(f"result_sidecar_invalid:{_relative(root, path)}")
    if errors:
        return errors
    try:
        result = ResearchResultEnvelope.model_validate_json(result_path.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return [f"result_invalid:{_relative(root, result_path)}:{exc}"]
    errors.extend(
        f"result_contract:{message}"
        for message in validate_result_for_preregistration(
            prereg, result, preregistration_sha256=digest(prereg_path)
        )
    )
    return errors


def audit_repository(root: Path) -> dict[str, Any]:
    grandfather_path = root / "data/factor_research/research/research_contract_grandfather_2026-11-24.json"
    grandfather = json.loads(grandfather_path.read_text(encoding="utf-8"))
    frozen = {row["path"]: row["sha256"] for row in grandfather["rows"]}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in runner_paths(root):
        relative = _relative(root, path)
        current = digest(path)
        assigned = top_level_assignments(path)
        declared_type = assigned.get("RESEARCH_TYPE")
        declared_prereg = assigned.get("RESEARCH_PREREGISTRATION")
        grandfathered = frozen.get(relative) == current
        if grandfathered:
            rows.append({
                "path": relative,
                "sha256": current,
                "status": "legacy_grandfathered",
                "contract_required": False,
            })
            continue
        if relative in frozen:
            errors.append(f"grandfathered_runner_changed:{relative}")
        if not isinstance(declared_type, str) or not isinstance(declared_prereg, str):
            errors.append(f"contract_declarations_missing:{relative}")
            rows.append({
                "path": relative,
                "sha256": current,
                "status": "contract_required",
                "contract_required": True,
            })
            continue
        prereg, prereg_error = _load_prereg(root, declared_prereg, current)
        if prereg_error:
            errors.append(prereg_error)
        if prereg is not None and prereg.research_type != declared_type:
            errors.append(f"research_type_mismatch:{relative}")
        result_errors = (
            _result_errors(root, prereg, root / declared_prereg)
            if prereg is not None
            else []
        )
        errors.extend(result_errors)
        rows.append({
            "path": relative,
            "sha256": current,
            "status": "contract_valid" if not prereg_error and not result_errors else "contract_invalid",
            "research_type": declared_type,
            "preregistration": declared_prereg,
            "contract_required": True,
        })
    return {
        "schema_version": 1,
        "generated_at": "2026-11-24",
        "gates": {
            "contract_errors": len(errors),
            "grandfathered": sum(row["status"] == "legacy_grandfathered" for row in rows),
            "contract_valid": sum(row["status"] == "contract_valid" for row in rows),
            "contract_invalid": sum(row["status"] == "contract_invalid" for row in rows),
            "contract_required": sum(row["status"] == "contract_required" for row in rows),
        },
        "errors": errors,
        "runners": rows,
    }


def main() -> int:
    payload = audit_repository(ROOT)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(raw, encoding="utf-8")
    sidecar(OUTPUT)
    print(json.dumps({"output": OUTPUT.relative_to(ROOT).as_posix(), **payload["gates"]}))
    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
