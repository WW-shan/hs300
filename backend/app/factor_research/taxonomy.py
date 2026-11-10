"""Validation for explicit factor-field research taxonomy."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_DATA_CLASSES = {"price", "sentiment", "flow", "industry", "macro", "financial"}
_CROSS_SECTION_TYPES = {
    "stock_cross_sectional",
    "industry_cross_sectional",
    "event_sparse",
    "market_level",
    "text_or_signal",
    "unknown",
}
_PIT_HINTS = {"verified", "declared", "not_applicable", "unknown"}


def validate_field_taxonomy(
    payload: Mapping[str, Any],
    *,
    expected_factor_ids: Iterable[str] | None = None,
) -> list[str]:
    """Return deterministic validation errors; an empty list means valid."""
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    records = payload.get("records")
    if not isinstance(records, list):
        return [*errors, "records must be a list"]

    seen: set[str] = set()
    for index, item in enumerate(records):
        prefix = f"records[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        factor_id = item.get("factor_id")
        if not isinstance(factor_id, str) or not factor_id:
            errors.append(f"{prefix}.factor_id must be a non-empty string")
        elif factor_id in seen:
            errors.append(f"{prefix}.factor_id is duplicated: {factor_id}")
        else:
            seen.add(factor_id)
        if item.get("data_class") not in _DATA_CLASSES:
            errors.append(f"{prefix}.data_class is invalid")
        if item.get("cross_section_type") not in _CROSS_SECTION_TYPES:
            errors.append(f"{prefix}.cross_section_type is invalid")
        if item.get("pit_hint") not in _PIT_HINTS:
            errors.append(f"{prefix}.pit_hint is invalid")
        asset_types = item.get("asset_types")
        if not isinstance(asset_types, list) or not asset_types or not all(
            isinstance(value, str) and value for value in asset_types
        ):
            errors.append(f"{prefix}.asset_types must be a non-empty string list")
        source_field = item.get("source_field")
        if not isinstance(source_field, str) or not source_field:
            errors.append(f"{prefix}.source_field must be a non-empty string")
        if not isinstance(item.get("zero_is_missing"), bool):
            errors.append(f"{prefix}.zero_is_missing must be a boolean")
        confidence = item.get("classification_confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            errors.append(f"{prefix}.classification_confidence must be within [0, 1]")
        staleness_override = item.get("staleness_days_override")
        if staleness_override is not None and (
            isinstance(staleness_override, bool)
            or not isinstance(staleness_override, int)
            or staleness_override <= 0
        ):
            errors.append(f"{prefix}.staleness_days_override must be a positive integer")
        evidence_refs = item.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            errors.append(f"{prefix}.evidence_refs must be non-empty")
        else:
            for evidence_index, evidence in enumerate(evidence_refs):
                evidence_prefix = f"{prefix}.evidence_refs[{evidence_index}]"
                if not isinstance(evidence, Mapping):
                    errors.append(f"{evidence_prefix} must be an object")
                    continue
                if not isinstance(evidence.get("path"), str) or not evidence.get("path"):
                    errors.append(f"{evidence_prefix}.path must be a non-empty string")
                digest = evidence.get("sha256")
                if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
                    errors.append(f"{evidence_prefix}.sha256 must be sha256:<64 hex>")

    if expected_factor_ids is not None:
        expected = {str(item) for item in expected_factor_ids}
        missing = sorted(expected - seen)
        if missing:
            errors.append(f"missing taxonomy records: {', '.join(missing)}")
    return errors
