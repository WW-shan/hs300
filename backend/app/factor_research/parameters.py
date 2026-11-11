"""Parameter-family registry, validation, and fail-closed factor classification."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from app.factor_research.fingerprints import sha256_json
from app.factor_research.models import (
    CatalogSnapshot,
    CrossSectionType,
    DataClass,
    Direction,
    FactorRecord,
    FactorResearchModel,
)

ParameterClassificationStatus = Literal[
    "tunable",
    "fixed",
    "grouped",
    "blocked",
    "unclassified",
]
ParameterFamilyStatus = Literal["tunable", "blocked"]

_WINDOW_PATTERNS = (
    re.compile(r"(?:^|_)(\d+)d(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)(?:ma|ema)(\d+)(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)(?:rsi|atr)_?(\d+)(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)(\d+)_(?:\d+)(?:_|$)", re.IGNORECASE),
    re.compile(r"(?:^|_)(\d+)[mq](?:_|$)", re.IGNORECASE),
    re.compile(r"_(\d+)$", re.IGNORECASE),
)


class ParameterFamily(FactorResearchModel):
    family_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    data_class: DataClass = "price"
    cross_section_type: CrossSectionType = "stock_cross_sectional"
    direction: Direction
    evaluator: str = Field(min_length=1)
    parameter_name: str = Field(default="window", min_length=1)
    parameter_grid: list[int] = Field(min_length=1)
    current_parameter: int = Field(gt=0)
    minimum_history: int = Field(default=0, ge=0)
    neutralization: list[Literal["industry", "size"]] = Field(default_factory=list)
    member_factor_ids: list[str] = Field(min_length=1)
    status: ParameterFamilyStatus = "tunable"
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("parameter_grid")
    @classmethod
    def _positive_parameter_grid(cls, value: list[int]) -> list[int]:
        if any(parameter <= 0 for parameter in value):
            raise ValueError("parameter grid values must be positive")
        return value

    @field_validator("member_factor_ids")
    @classmethod
    def _nonempty_member_ids(cls, value: list[str]) -> list[str]:
        if any(not factor_id for factor_id in value):
            raise ValueError("member factor ids must be nonempty")
        return value


class ParameterFamilyRegistry(FactorResearchModel):
    schema_version: Literal[1] = 1
    families: list[ParameterFamily] = Field(default_factory=list)
    unclassified_factor_ids: list[str] = Field(default_factory=list)
    blocked_factor_ids: list[str] = Field(default_factory=list)


class FactorParameterClassification(FactorResearchModel):
    factor_id: str
    factor_version: int
    status: ParameterClassificationStatus
    family_id: str | None = None
    parameter: int | None = None
    reason: str


def load_parameter_registry(data_dir: Path) -> ParameterFamilyRegistry:
    path = Path(data_dir) / "factor_research" / "parameters" / "families.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ParameterFamilyRegistry.model_validate(payload)


def _duplicates(values: list[str]) -> set[str]:
    return {value for value, count in Counter(values).items() if count > 1}


def validate_parameter_registry(
    registry: ParameterFamilyRegistry,
    factor_ids: set[str],
) -> list[str]:
    errors: list[str] = []
    family_by_id = Counter(family.family_id for family in registry.families)
    for family_id, count in sorted(family_by_id.items()):
        if count > 1:
            errors.append(f"duplicate family id: {family_id}")

    member_owner: dict[str, str] = {}
    for family in registry.families:
        for factor_id in _duplicates(family.member_factor_ids):
            errors.append(f"duplicate member within family {family.family_id}: {factor_id}")
        for factor_id in family.member_factor_ids:
            previous = member_owner.get(factor_id)
            if previous is not None:
                errors.append(
                    f"duplicate member {factor_id} in families {previous} and {family.family_id}"
                )
            else:
                member_owner[factor_id] = family.family_id
            if factor_id not in factor_ids:
                errors.append(f"missing factor {factor_id} from family {family.family_id}")
        grid = family.parameter_grid
        if grid != sorted(grid) or len(set(grid)) != len(grid):
            errors.append(
                f"parameter grid must be strictly ascending for family {family.family_id}"
            )
        if family.current_parameter not in grid:
            errors.append(
                f"current parameter {family.current_parameter} not in parameter grid "
                f"for family {family.family_id}"
            )

    for field_name in ("unclassified_factor_ids", "blocked_factor_ids"):
        for factor_id in getattr(registry, field_name):
            if factor_id not in factor_ids:
                errors.append(f"missing factor {factor_id} in {field_name}")
        for factor_id in _duplicates(getattr(registry, field_name)):
            errors.append(f"duplicate factor {factor_id} in {field_name}")

    manual_states = set(registry.unclassified_factor_ids) | set(registry.blocked_factor_ids)
    for factor_id in sorted(manual_states.intersection(member_owner)):
        errors.append(
            f"factor {factor_id} is both a family member and manually classified"
        )
    overlap = set(registry.unclassified_factor_ids).intersection(registry.blocked_factor_ids)
    for factor_id in sorted(overlap):
        errors.append(f"factor {factor_id} is both unclassified and blocked")
    return errors


def detect_parameter_window(factor_id: str) -> int | None:
    for pattern in _WINDOW_PATTERNS:
        match = pattern.search(factor_id)
        if match is not None:
            return int(match.group(1))
    return None


def classify_factor_parameters(
    factors: list[FactorRecord],
    registry: ParameterFamilyRegistry,
) -> list[FactorParameterClassification]:
    validation_errors = validate_parameter_registry(
        registry,
        {factor.factor_id for factor in factors},
    )
    if validation_errors:
        raise ValueError("; ".join(validation_errors))

    family_by_member = {
        factor_id: family
        for family in registry.families
        for factor_id in family.member_factor_ids
    }
    blocked_ids = set(registry.blocked_factor_ids)
    unclassified_ids = set(registry.unclassified_factor_ids)
    rows: list[FactorParameterClassification] = []
    for factor in sorted(factors, key=lambda item: item.factor_id):
        family = family_by_member.get(factor.factor_id)
        parameter = detect_parameter_window(factor.factor_id)
        if (
            factor.factor_id in blocked_ids
            or factor.future_data_status == "blocked"
            or factor.definition_status != "available"
            or (family is not None and family.status == "blocked")
        ):
            status: ParameterClassificationStatus = "blocked"
            reason = (
                "member of blocked parameter family"
                if family is not None and family.status == "blocked"
                else "factor definition or future-data status is blocked"
            )
        elif family is not None:
            status = "grouped"
            reason = f"member of parameter family {family.family_id}"
        elif factor.factor_id in unclassified_ids:
            status = "unclassified"
            reason = "explicitly listed as unclassified"
        elif parameter is not None:
            status = "unclassified"
            reason = "numeric parameter window is not registered in a parameter family"
        else:
            status = "fixed"
            reason = "no tunable numeric window detected and no family membership"
        rows.append(
            FactorParameterClassification(
                factor_id=factor.factor_id,
                factor_version=factor.factor_version,
                status=status,
                family_id=family.family_id if family is not None else None,
                parameter=parameter,
                reason=reason,
            )
        )
    return rows


def build_classification_payload(
    catalog: CatalogSnapshot,
    registry: ParameterFamilyRegistry,
    *,
    generated_at: str,
) -> dict[str, Any]:
    errors = validate_parameter_registry(
        registry,
        {factor.factor_id for factor in catalog.factors},
    )
    if errors:
        raise ValueError("; ".join(errors))
    classifications = classify_factor_parameters(catalog.factors, registry)
    counts = Counter(row.status for row in classifications)
    family_rows = [
        {
            **family.model_dump(mode="json"),
            "member_count": len(family.member_factor_ids),
        }
        for family in sorted(registry.families, key=lambda item: item.family_id)
    ]
    factor_rows = [row.model_dump(mode="json") for row in classifications]
    report_id = sha256_json({
        "catalog_snapshot_id": catalog.snapshot_id,
        "catalog_registry_fingerprint": catalog.registry_fingerprint,
        "parameter_registry": registry.model_dump(mode="json"),
        "classification": factor_rows,
    })
    return {
        "schema_version": 1,
        "report_id": report_id,
        "generated_at": generated_at,
        "catalog": {
            "snapshot_id": catalog.snapshot_id,
            "registry_fingerprint": catalog.registry_fingerprint,
            "factor_count": catalog.factor_count,
            "source_completeness": catalog.source_completeness,
        },
        "registry": {
            "schema_version": registry.schema_version,
            "family_count": len(registry.families),
            "member_count": sum(len(family.member_factor_ids) for family in registry.families),
        },
        "counts": {
            "total": len(classifications),
            "tunable": counts.get("tunable", 0),
            "fixed": counts.get("fixed", 0),
            "grouped": counts.get("grouped", 0),
            "blocked": counts.get("blocked", 0),
            "unclassified": counts.get("unclassified", 0),
        },
        "families": family_rows,
        "factors": factor_rows,
        "validation_errors": [],
    }
