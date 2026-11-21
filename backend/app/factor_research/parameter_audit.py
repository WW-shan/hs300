"""Read-only audit that joins parameter families, MC evidence and current sets."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PARAMETER_RESULTS = (
    "p1_parameter_mc_2026-11-11.json",
    "p2_builtin_parameter_mc_2026-11-11_v2.json",
    "p3_extended_parameter_mc_2026-11-11_v3.json",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _family_results(root: Path) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    result_root = root.parent / "research" / "parameters"
    for name in PARAMETER_RESULTS:
        path = result_root / name
        if not path.exists():
            continue
        payload = _load(path)
        for family_id, result in payload.get("families", {}).items():
            merged[str(family_id)] = {
                "source_result": name,
                "recommended_parameter": result.get("recommended_parameter"),
                "recommendation_status": result.get("recommendation_status"),
                "recommended_trial": result.get("recommended_trial"),
            }
    return merged


def _current_set_members(root: Path) -> dict[str, str]:
    set_root = root.parent / "sets" / "multi_dimension_v1"
    head_path = set_root / "head.json"
    if not head_path.exists():
        return {}
    head = _load(head_path)
    version = head.get("watch_version")
    if version is None:
        return {}
    version_path = set_root / "versions" / f"v{int(version):04d}.json"
    if not version_path.exists():
        return {}
    payload = _load(version_path)
    return {
        member["factor_id"]: member.get("direction")
        for member in payload.get("members", [])
        if member.get("role") == "alpha"
    }


def build_parameter_audit_summary(*, data_dir: Path) -> dict[str, Any]:
    root = Path(data_dir) / "factor_research" / "parameters"
    registry = _load(root / "families.json")
    results = _family_results(root)
    current_members = _current_set_members(root)
    families = []
    missing = []
    orphan = []
    for family in registry.get("families", []):
        family_id = str(family["family_id"])
        evidence = results.get(family_id)
        members = [str(item) for item in family.get("member_factor_ids", [])]
        current_member = next((member for member in members if member in current_members), None)
        row = {
            "family_id": family_id,
            "label": family.get("label"),
            "data_class": family.get("data_class"),
            "current_parameter": family.get("current_parameter"),
            "parameter_grid": family.get("parameter_grid"),
            "member_factor_ids": members,
            "current_set_member": current_member,
            "current_set_direction": current_members.get(current_member) if current_member else None,
            "covered": evidence is not None,
            "source_result": evidence.get("source_result") if evidence else None,
            "recommended_parameter": evidence.get("recommended_parameter") if evidence else None,
            "recommendation_status": evidence.get("recommendation_status") if evidence else None,
        }
        if evidence is None:
            missing.append(family_id)
        elif evidence.get("recommended_parameter") is not None and current_member is None:
            orphan.append({
                "family_id": family_id,
                "recommended_parameter": evidence["recommended_parameter"],
                "member_factor_ids": members,
            })
        families.append(row)
    registry_ids = {str(family["family_id"]) for family in registry.get("families", [])}
    extra_results = sorted(set(results) - registry_ids)
    return {
        "registry_family_count": len(registry_ids),
        "covered_family_count": len(registry_ids) - len(missing),
        "missing_family_ids": missing,
        "extra_result_family_ids": extra_results,
        "current_set_members": current_members,
        "families": families,
        "orphan_recommendations": orphan,
        "recommendation_count": sum(row["recommended_parameter"] is not None for row in families),
        "rejected_count": sum(row["recommendation_status"] == "rejected" for row in families),
        "current_set_tunable_families": [row for row in families if row["current_set_member"]],
    }
