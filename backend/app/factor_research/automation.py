"""Fail-closed automation primitives for the factor research pipeline."""
from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import sha256_file, sha256_json
from app.factor_research.ledger import ResearchLedger
from app.factor_research.models import CatalogSnapshot, LedgerRecord
from app.factor_research.parameters import (
    FactorParameterClassification,
    ParameterFamily,
    classify_factor_parameters,
    detect_parameter_window,
    validate_parameter_registry,
)
from app.factor_research.threshold_parameters import (
    ThresholdFamily,
    validate_threshold_registry,
)


def automation_research_id(payload: Mapping[str, Any]) -> str:
    """Hash a deterministic automation payload, excluding run-time fields."""
    core = {
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }
    return sha256_json(core)


def _audit_status(
    factor_id: str,
    audit: Mapping[str, Any] | None,
    *,
    current_config_sha256: str | None,
) -> str:
    if audit is None:
        return "audit_missing"
    manifest = audit.get("manifest")
    findings = audit.get("findings")
    if not isinstance(manifest, Mapping) or not isinstance(findings, Mapping):
        return "audit_missing"
    fingerprints = manifest.get("input_fingerprints")
    ext_fingerprint = (
        fingerprints.get("ext_data") if isinstance(fingerprints, Mapping) else None
    )
    audited_config_sha256 = (
        ext_fingerprint.get("config_sha256")
        if isinstance(ext_fingerprint, Mapping)
        else None
    )
    if not isinstance(audited_config_sha256, str) or not current_config_sha256:
        return "audit_unverifiable"
    if audited_config_sha256 != current_config_sha256:
        return "audit_stale"
    finding = findings.get(factor_id)
    if not isinstance(finding, Mapping):
        return "audit_missing"
    if finding.get("integrity") != "pass":
        return "audit_failed"
    return "current"


def evaluate_factor_readiness(
    catalog: CatalogSnapshot,
    classifications: Sequence[FactorParameterClassification],
    audit: Mapping[str, Any] | None,
    *,
    current_audit_config_sha256: str | None,
) -> dict[str, dict[str, Any]]:
    """Evaluate research and portfolio readiness for every catalog factor."""
    classification_by_id = {row.factor_id: row for row in classifications}
    readiness: dict[str, dict[str, Any]] = {}
    for factor in catalog.factors:
        classification = classification_by_id.get(factor.factor_id)
        scan_reasons: list[str] = []
        portfolio_reasons: list[str] = []
        if classification is None:
            scan_reasons.append("classification_missing")
        elif classification.status == "unclassified":
            scan_reasons.append("classification_unclassified")
        elif classification.status == "blocked":
            scan_reasons.append("classification_blocked")

        if factor.future_data_status != "clear":
            scan_reasons.append(f"future_data_{factor.future_data_status}")
        if factor.pit_status == "unknown":
            scan_reasons.append("pit_status_unknown")
        if factor.source.type in {"custom", "composite"}:
            scan_reasons.append("source_not_automatable")

        if factor.source.type == "extension":
            audit_status = _audit_status(
                factor.factor_id,
                audit,
                current_config_sha256=current_audit_config_sha256,
            )
            if audit_status != "current":
                scan_reasons.append(audit_status)
        elif factor.source.type == "builtin":
            audit_status = "not_required"
        else:
            audit_status = "not_required"

        if factor.data_class == "financial" and factor.pit_status != "verified":
            portfolio_reasons.append("financial_pit_not_verified")

        scan_reasons = sorted(set(scan_reasons))
        reasons = sorted(set([*scan_reasons, *portfolio_reasons]))
        scan_ready = not scan_reasons
        readiness[factor.factor_id] = {
            "scan_ready": scan_ready,
            "portfolio_ready": scan_ready and not portfolio_reasons,
            "reasons": reasons,
            "audit_status": audit_status,
            "classification_status": (
                classification.status if classification is not None else "missing"
            ),
            "pit_status": factor.pit_status,
            "future_data_status": factor.future_data_status,
        }
    return readiness


def _window_parameter_registered(family: ParameterFamily, parameter: float) -> bool:
    for factor_id in family.member_factor_ids:
        detected = detect_parameter_window(factor_id)
        if detected is not None and math.isclose(float(detected), parameter):
            return True
        if (
            detected is None
            and len(family.member_factor_ids) == 1
            and math.isclose(float(family.current_parameter), parameter)
        ):
            return True
    return False


def _p5_candidate_current(candidate: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    source = candidate.get("source_research")
    if not isinstance(source, str):
        return False
    return payload.get(f"{source}_research_id") == candidate.get("research_id")


def build_research_coverage(
    window_payloads: Mapping[str, Mapping[str, Any]],
    threshold_payload: Mapping[str, Any],
    incremental_payload: Mapping[str, Any],
    window_families: Sequence[ParameterFamily],
    threshold_families: Sequence[ThresholdFamily],
) -> dict[str, dict[str, Any]]:
    """Map every registered family to its published research and incremental status."""
    p5_candidates = incremental_payload.get("candidates")
    candidates = p5_candidates if isinstance(p5_candidates, list) else []
    coverage: dict[str, dict[str, Any]] = {}

    for family in window_families:
        result = None
        source_research = None
        for source in ("p1", "p2", "p3"):
            payload = window_payloads.get(source)
            if not isinstance(payload, Mapping):
                continue
            families = payload.get("families")
            if isinstance(families, Mapping) and isinstance(families.get(family.family_id), Mapping):
                result = families[family.family_id]
                source_research = source
                break
        entry: dict[str, Any] = {
            "family_id": family.family_id,
            "kind": "window",
            "label": family.label,
            "source_research": source_research,
            "research_id": None,
            "recommendation_status": None,
            "recommended_parameter": None,
            "recommended_variant_id": None,
            "incremental_status": None,
            "incremental_candidate_key": None,
            "research_status": "parameter_scan_pending",
        }
        if result is not None and source_research is not None:
            payload = window_payloads[source_research]
            recommended = result.get("recommended_parameter")
            status = str(result.get("recommendation_status") or "unknown")
            entry.update(
                {
                    "research_id": payload.get("research_id"),
                    "recommendation_status": status,
                    "recommended_parameter": recommended,
                    "recommended_variant_id": result.get("recommended_variant_id"),
                }
            )
            if status != "recommended" or not isinstance(recommended, (int, float)):
                entry["research_status"] = "no_recommendation"
            else:
                candidate = next(
                    (
                        item
                        for item in candidates
                        if isinstance(item, Mapping)
                        and _p5_candidate_current(item, incremental_payload)
                        and item.get("family_id") == family.family_id
                        and isinstance(item.get("parameter"), (int, float))
                        and math.isclose(float(item["parameter"]), float(recommended))
                    ),
                    None,
                )
                if candidate is not None:
                    entry["research_status"] = "incremental_researched"
                    entry["incremental_status"] = candidate.get("status")
                    entry["incremental_candidate_key"] = candidate.get("candidate_key")
                elif _window_parameter_registered(family, float(recommended)):
                    entry["research_status"] = "incremental_pending"
                else:
                    entry["research_status"] = "registration_pending"
        coverage[family.family_id] = entry

    for family in threshold_families:
        families = threshold_payload.get("families")
        result = (
            families.get(family.family_id)
            if isinstance(families, Mapping)
            and isinstance(families.get(family.family_id), Mapping)
            else None
        )
        entry = {
            "family_id": family.family_id,
            "kind": family.kind,
            "label": family.label,
            "source_research": "p4" if result is not None else None,
            "research_id": threshold_payload.get("research_id") if result else None,
            "recommendation_status": None,
            "recommended_parameter": None,
            "recommended_variant_id": None,
            "incremental_status": None,
            "incremental_candidate_key": None,
            "research_status": "parameter_scan_pending",
        }
        if result is not None:
            status = str(result.get("recommendation_status") or "unknown")
            entry.update(
                {
                    "recommendation_status": status,
                    "recommended_parameter": result.get("recommended_parameter"),
                    "recommended_variant_id": result.get("recommended_variant_id"),
                }
            )
            if status != "recommended":
                entry["research_status"] = "no_recommendation"
            else:
                candidate = next(
                    (
                        item
                        for item in candidates
                        if isinstance(item, Mapping)
                        and _p5_candidate_current(item, incremental_payload)
                        and item.get("source_factor_id") == family.source_factor_id
                        and item.get("source_research") in {"p3", "p4"}
                    ),
                    None,
                )
                if candidate is not None:
                    entry["research_status"] = "incremental_researched"
                    entry["incremental_status"] = candidate.get("status")
                    entry["incremental_candidate_key"] = candidate.get("candidate_key")
                else:
                    entry["research_status"] = "incremental_pending"
        coverage[family.family_id] = entry
    return coverage


def _family_factor_ids(
    family: ParameterFamily | ThresholdFamily,
) -> list[str]:
    if isinstance(family, ParameterFamily):
        return list(family.member_factor_ids)
    if family.source_factor_id:
        return [family.source_factor_id]
    return []


def _blocked_family_task(
    family: ParameterFamily | ThresholdFamily,
    readiness: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    reasons_by_factor = {
        factor_id: list(readiness.get(factor_id, {}).get("reasons") or ["readiness_missing"])
        for factor_id in _family_factor_ids(family)
        if readiness.get(factor_id, {}).get("scan_ready") is not True
    }
    if not reasons_by_factor:
        return None
    kind = "window" if isinstance(family, ParameterFamily) else family.kind
    return {
        "task_id": f"family_admission:{kind}:{family.family_id}",
        "kind": "family_admission",
        "status": "blocked",
        "family_id": family.family_id,
        "family_kind": kind,
        "reasons_by_factor": reasons_by_factor,
    }


def _coverage_task(
    coverage: Mapping[str, Any],
) -> dict[str, Any] | None:
    status = coverage.get("research_status")
    kind = coverage.get("kind")
    family_id = coverage.get("family_id")
    if status == "parameter_scan_pending":
        return {
            "task_id": f"parameter_scan:{kind}:{family_id}",
            "kind": "parameter_scan",
            "status": "pending",
            "family_id": family_id,
            "family_kind": kind,
        }
    if status == "incremental_pending":
        return {
            "task_id": f"incremental_test:{kind}:{family_id}",
            "kind": "incremental_test",
            "status": "pending",
            "family_id": family_id,
            "family_kind": kind,
            "research_id": coverage.get("research_id"),
            "recommended_parameter": coverage.get("recommended_parameter"),
        }
    if status == "registration_pending":
        return {
            "task_id": f"factor_registration:{kind}:{family_id}",
            "kind": "factor_registration",
            "status": "pending",
            "family_id": family_id,
            "family_kind": kind,
            "research_id": coverage.get("research_id"),
            "recommended_parameter": coverage.get("recommended_parameter"),
        }
    return None


def _audit_metadata(
    audit: Mapping[str, Any] | None,
    *,
    current_config_sha256: str | None,
) -> dict[str, Any]:
    manifest = audit.get("manifest") if audit else None
    fingerprints = manifest.get("input_fingerprints") if isinstance(manifest, Mapping) else None
    ext_fingerprint = (
        fingerprints.get("ext_data") if isinstance(fingerprints, Mapping) else None
    )
    audited_config_sha256 = (
        ext_fingerprint.get("config_sha256")
        if isinstance(ext_fingerprint, Mapping)
        else None
    )
    return {
        "audit_id": manifest.get("audit_id") if isinstance(manifest, Mapping) else None,
        "audited_config_sha256": audited_config_sha256,
        "current_config_sha256": current_config_sha256,
        "config_current": bool(
            isinstance(audited_config_sha256, str)
            and current_config_sha256
            and audited_config_sha256 == current_config_sha256
        ),
    }


def build_automation_payload(
    *,
    catalog: CatalogSnapshot,
    window_registry: Any,
    threshold_registry: Any,
    window_payloads: Mapping[str, Mapping[str, Any]],
    threshold_payload: Mapping[str, Any],
    incremental_payload: Mapping[str, Any],
    ledger_bindings: Sequence[Mapping[str, Any]],
    previous_payload: Mapping[str, Any] | None,
    current_audit_config_sha256: str | None,
    audit: Mapping[str, Any] | None,
    generated_at: str,
) -> dict[str, Any]:
    """Build the complete deterministic P6 automation payload."""
    factor_ids = {factor.factor_id for factor in catalog.factors}
    registry_errors = validate_parameter_registry(window_registry, factor_ids)
    registry_errors.extend(
        validate_threshold_registry(threshold_registry, catalog, window_registry)
    )
    if registry_errors:
        raise ValueError("; ".join(sorted(set(registry_errors))))

    classifications = classify_factor_parameters(catalog.factors, window_registry)
    classification_by_id = {row.factor_id: row for row in classifications}
    unclassified_ids = sorted(
        factor_id
        for factor_id, row in classification_by_id.items()
        if row.status == "unclassified"
    )
    readiness = evaluate_factor_readiness(
        catalog,
        classifications,
        audit,
        current_audit_config_sha256=current_audit_config_sha256,
    )
    coverage = build_research_coverage(
        window_payloads,
        threshold_payload,
        incremental_payload,
        window_registry.families,
        threshold_registry.families,
    )

    pending_tasks: list[dict[str, Any]] = []
    for family in [*window_registry.families, *threshold_registry.families]:
        blocked = _blocked_family_task(family, readiness)
        if blocked is not None:
            pending_tasks.append(blocked)
            continue
        task = _coverage_task(coverage[family.family_id])
        if task is not None:
            pending_tasks.append(task)
    for factor_id in unclassified_ids:
        pending_tasks.append(
            {
                "task_id": f"classification:{factor_id}",
                "kind": "classification",
                "status": "blocked",
                "factor_id": factor_id,
                "reasons": list(readiness[factor_id]["reasons"]),
            }
        )

    previous_factor_ids = set()
    delta_mode = "baseline"
    previous_research_id = None
    previous_catalog = previous_payload.get("catalog") if previous_payload else None
    previous_snapshot_id = (
        previous_catalog.get("snapshot_id")
        if isinstance(previous_catalog, Mapping)
        else None
    )
    if previous_payload is not None and previous_snapshot_id != catalog.snapshot_id:
        delta_mode = "incremental"
        previous_research_id = previous_payload.get("research_id")
        if isinstance(previous_catalog, Mapping):
            raw_ids = previous_catalog.get("factor_ids")
            if isinstance(raw_ids, list):
                previous_factor_ids = {str(item) for item in raw_ids}
    new_factor_ids = sorted(factor_ids - previous_factor_ids) if delta_mode == "incremental" else []
    removed_factor_ids = sorted(previous_factor_ids - factor_ids) if delta_mode == "incremental" else []
    family_member_ids = {
        factor_id
        for family in window_registry.families
        for factor_id in family.member_factor_ids
    }
    for factor_id in new_factor_ids:
        row = readiness[factor_id]
        classification = classification_by_id[factor_id]
        if factor_id not in family_member_ids and classification.status not in {
            "unclassified",
            "blocked",
            "grouped",
        } and row["audit_status"] in {"audit_missing", "audit_failed", "audit_unverifiable"}:
            pending_tasks.append(
                {
                    "task_id": f"factor_audit:{factor_id}",
                    "kind": "factor_audit",
                    "status": "blocked",
                    "factor_id": factor_id,
                    "reasons": [row["audit_status"]],
                }
            )

    pending_tasks.sort(key=lambda item: item["task_id"])
    negative_knowledge = [
        {
            "task_id": task["task_id"],
            "kind": task["kind"],
            "status": task["status"],
            "reasons": task.get("reasons")
            or sorted(
                {
                    reason
                    for reasons in task.get("reasons_by_factor", {}).values()
                    for reason in reasons
                }
            ),
        }
        for task in pending_tasks
        if task["status"] == "blocked"
    ]

    classification_counts = Counter(row.status for row in classifications)
    research_status_counts = Counter(
        row["research_status"] for row in coverage.values()
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "method_version": "factor_automation_p6_v1",
        "generated_at": generated_at,
        "catalog": {
            "snapshot_id": catalog.snapshot_id,
            "registry_fingerprint": catalog.registry_fingerprint,
            "source_completeness": catalog.source_completeness,
            "factor_count": catalog.factor_count,
            "factor_ids": sorted(factor_ids),
        },
        "catalog_delta": {
            "mode": delta_mode,
            "previous_research_id": previous_research_id,
            "new_factor_ids": new_factor_ids,
            "removed_factor_ids": removed_factor_ids,
        },
        "registries": {
            "window_family_count": len(window_registry.families),
            "threshold_family_count": len(threshold_registry.families),
        },
        "classification": {
            "counts": dict(sorted(classification_counts.items())),
            "unclassified_factor_ids": unclassified_ids,
        },
        "audit": _audit_metadata(
            audit,
            current_config_sha256=current_audit_config_sha256,
        ),
        "ledger_bindings": [dict(item) for item in ledger_bindings],
        "window_families": [
            coverage[family.family_id]
            for family in sorted(window_registry.families, key=lambda item: item.family_id)
        ],
        "threshold_families": [
            coverage[family.family_id]
            for family in sorted(threshold_registry.families, key=lambda item: item.family_id)
        ],
        "coverage": {
            "status_counts": dict(sorted(research_status_counts.items())),
            "researched_family_count": sum(
                1
                for row in coverage.values()
                if row["research_status"]
                in {"incremental_researched", "no_recommendation"}
            ),
        },
        "admission": readiness,
        "pending_tasks": pending_tasks,
        "negative_knowledge": negative_knowledge,
        "summary": {
            "factor_count": len(catalog.factors),
            "scan_ready_factor_count": sum(
                1 for row in readiness.values() if row["scan_ready"]
            ),
            "portfolio_ready_factor_count": sum(
                1 for row in readiness.values() if row["portfolio_ready"]
            ),
            "unclassified_count": len(unclassified_ids),
            "pending_task_count": sum(
                1 for task in pending_tasks if task["status"] == "pending"
            ),
            "blocked_task_count": sum(
                1 for task in pending_tasks if task["status"] == "blocked"
            ),
            "new_factor_count": len(new_factor_ids),
            "active_version": None,
            "orders_generated": False,
        },
    }
    payload["research_id"] = automation_research_id(payload)
    return payload


def _resolve_data_path(data_dir: Path, raw_path: str, *, field: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe automation {field}: {raw_path}")
    resolved = (data_dir / relative).resolve()
    root = data_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"automation {field} escapes data dir: {raw_path}")
    return resolved


def assert_portfolio_candidates_admitted(
    data_dir: Path,
    factor_ids: Sequence[str],
) -> dict[str, Any]:
    """Fail closed unless every requested factor is admitted by the latest P6 run."""
    data_dir = Path(data_dir)
    latest_path = data_dir / "factor_research" / "automation" / "latest.json"
    if not latest_path.exists():
        raise FileNotFoundError("automation latest manifest is unavailable")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    run_json = latest.get("run_json")
    research_id = latest.get("research_id")
    if not isinstance(run_json, str) or not isinstance(research_id, str):
        raise ValueError("automation latest manifest is malformed")
    run_path = _resolve_data_path(data_dir, run_json, field="run_json")
    if not run_path.exists():
        raise FileNotFoundError(run_path)
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    if payload.get("research_id") != research_id:
        raise ValueError("automation run research ID does not match latest pointer")
    if automation_research_id(payload) != research_id:
        raise ValueError("automation run research ID does not match canonical payload")
    admission = payload.get("admission")
    if not isinstance(admission, Mapping):
        raise ValueError("automation run admission manifest is missing")

    rejected: dict[str, list[str]] = {}
    for factor_id in factor_ids:
        row = admission.get(factor_id)
        if not isinstance(row, Mapping):
            rejected[factor_id] = ["admission_missing"]
            continue
        if row.get("portfolio_ready") is not True:
            reasons = row.get("reasons")
            rejected[factor_id] = (
                [str(reason) for reason in reasons]
                if isinstance(reasons, list) and reasons
                else ["portfolio_not_ready"]
            )
    if rejected:
        details = "; ".join(
            f"{factor_id}: {', '.join(reasons)}"
            for factor_id, reasons in sorted(rejected.items())
        )
        raise ValueError(f"portfolio admission rejected: {details}")
    return {
        "research_id": research_id,
        "run_json": run_json,
        "catalog_snapshot_id": payload.get("catalog", {}).get("snapshot_id"),
    }


def _evidence_fingerprint(
    data_dir: Path,
    evidence_paths: Sequence[Path],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs: list[dict[str, str]] = []
    fingerprints: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in evidence_paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = data_dir / path
        resolved = path.resolve()
        root = data_dir.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"research evidence escapes data dir: {raw_path}")
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        relative = resolved.relative_to(root).as_posix()
        if relative in seen:
            raise ValueError(f"duplicate research evidence path: {relative}")
        seen.add(relative)
        digest = sha256_file(resolved)
        refs.append({"kind": "research_evidence", "path": relative})
        fingerprints.append({"path": relative, "sha256": digest})
    return refs, fingerprints


def _matching_binding(
    data_dir: Path,
    *,
    operation: str,
    research_id: str,
    refs: Sequence[Mapping[str, str]],
    fingerprints: Sequence[Mapping[str, str]],
) -> LedgerRecord | None:
    ledger = ResearchLedger(data_dir)
    matches = [
        record
        for record in ledger.records()
        if record.operation == operation
        and record.input_fingerprints.get("research_id") == research_id
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple ledger bindings for {operation}/{research_id}")
    expected_paths = [str(item["path"]) for item in refs]
    for record in ledger.records():
        record_paths = [str(item.get("path")) for item in record.evidence_refs]
        overlap = set(expected_paths).intersection(record_paths)
        if overlap and (
            record.operation != operation
            or record.input_fingerprints.get("research_id") != research_id
        ):
            raise ValueError(
                f"research evidence already bound to another run: {sorted(overlap)}"
            )
    if not matches:
        return None
    record = matches[0]
    if [str(item.get("path")) for item in record.evidence_refs] != expected_paths:
        raise ValueError(f"research evidence path mismatch for {research_id}")
    stored_evidence = record.input_fingerprints.get("evidence_sha256")
    if stored_evidence is not None:
        if stored_evidence != [dict(item) for item in fingerprints]:
            raise ValueError(f"research evidence hash mismatch for {research_id}")
    else:
        expected_hashes = [
            record.input_fingerprints.get("output_sha256"),
            record.input_fingerprints.get("markdown_sha256"),
        ]
        if not fingerprints or any(
            index >= len(expected_hashes)
            or expected_hashes[index] != item["sha256"]
            for index, item in enumerate(fingerprints)
        ):
            raise ValueError(f"research evidence hash mismatch for {research_id}")
    return record


def bind_research_evidence(
    data_dir: Path,
    *,
    operation: str,
    evidence_paths: Sequence[Path],
    research_id: str,
    payload: Mapping[str, Any],
) -> tuple[LedgerRecord, bool]:
    """Append one research run to ledger, or reuse its exact existing binding."""
    data_dir = Path(data_dir)
    refs, fingerprints = _evidence_fingerprint(data_dir, evidence_paths)
    existing = _matching_binding(
        data_dir,
        operation=operation,
        research_id=research_id,
        refs=refs,
        fingerprints=fingerprints,
    )
    if existing is not None:
        return existing, False

    record_payload = dict(payload)
    input_fingerprints = dict(record_payload.get("input_fingerprints") or {})
    input_fingerprints.update(
        {
            "research_id": research_id,
            "evidence_sha256": [dict(item) for item in fingerprints],
        }
    )
    if len(fingerprints) == 1:
        input_fingerprints["output_sha256"] = fingerprints[0]["sha256"]
    else:
        input_fingerprints["output_sha256"] = fingerprints[0]["sha256"]
        input_fingerprints["markdown_sha256"] = fingerprints[1]["sha256"]
    record_payload["input_fingerprints"] = input_fingerprints
    record_payload["evidence_refs"] = refs
    record = ResearchLedger(data_dir).append(operation, payload=record_payload)
    return record, True


def audit_research_bindings(
    data_dir: Path,
    bindings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Verify that every named research payload has one exact ledger binding."""
    data_dir = Path(data_dir)
    audited: list[dict[str, Any]] = []
    for binding in bindings:
        name = str(binding.get("name") or "")
        operation = str(binding.get("operation") or "")
        research_id = str(binding.get("research_id") or "")
        raw_paths = binding.get("evidence_paths")
        if not name or not operation or not research_id or not isinstance(raw_paths, list):
            raise ValueError("research binding declaration is malformed")
        refs, fingerprints = _evidence_fingerprint(
            data_dir,
            [Path(str(path)) for path in raw_paths],
        )
        record = _matching_binding(
            data_dir,
            operation=operation,
            research_id=research_id,
            refs=refs,
            fingerprints=fingerprints,
        )
        if record is None:
            raise ValueError(f"research ledger binding is missing: {name}/{research_id}")
        audited.append(
            {
                "name": name,
                "operation": operation,
                "research_id": research_id,
                "record_id": record.record_id,
                "record_hash": record.record_hash,
            }
        )
    return audited
