"""Pre-registered threshold and event-holding parameter families."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.factor_research.models import (
    CatalogSnapshot,
    DataClass,
    Direction,
    FactorResearchModel,
)
from app.factor_research.parameters import (
    ParameterFamilyRegistry,
    classify_factor_parameters,
)

ThresholdKind = Literal["threshold", "event_holding"]
ThresholdMode = Literal["absolute", "quantile"]


class ThresholdFamily(FactorResearchModel):
    family_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: ThresholdKind
    source_factor_id: str | None = None
    event_key: str | None = None
    mode: ThresholdMode | None = None
    direction: Direction = "high"
    parameter_name: str = Field(min_length=1)
    parameter_grid: list[float] = Field(min_length=1)
    current_parameter: float
    neutralization: list[Literal["industry", "size"]] = Field(default_factory=list)
    data_class: DataClass = "unknown"
    minimum_history: int = Field(default=0, ge=0)
    formula_template: str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("parameter_grid")
    @classmethod
    def _ordered_finite_grid(cls, value: list[float]) -> list[float]:
        if any(not math.isfinite(parameter) for parameter in value):
            raise ValueError("parameter grid values must be finite")
        if value != sorted(value) or len(set(value)) != len(value):
            raise ValueError("parameter grid must be strictly ascending")
        return value

    @model_validator(mode="after")
    def _validate_family_contract(self) -> ThresholdFamily:
        if self.current_parameter not in self.parameter_grid:
            raise ValueError("current parameter must be in the parameter grid")
        if self.kind == "threshold":
            if not self.source_factor_id:
                raise ValueError("threshold family requires a source factor")
            if self.mode is None:
                raise ValueError("threshold family requires a mode")
            if self.event_key is not None:
                raise ValueError("threshold family cannot declare an event key")
            if self.mode == "quantile" and any(
                not 0.0 < parameter < 1.0 for parameter in self.parameter_grid
            ):
                raise ValueError("quantile thresholds must be inside (0, 1)")
        else:
            if not self.event_key:
                raise ValueError("event holding family requires an event key")
            if self.mode is not None:
                raise ValueError("event holding family cannot declare a threshold mode")
            if any(
                parameter <= 0.0 or not parameter.is_integer()
                for parameter in self.parameter_grid
            ):
                raise ValueError("holding days must be positive integers")
        return self


class ThresholdRegistry(FactorResearchModel):
    schema_version: Literal[1] = 1
    families: list[ThresholdFamily] = Field(default_factory=list)


def load_threshold_registry(data_dir: Path) -> ThresholdRegistry:
    path = Path(data_dir) / "factor_research" / "parameters" / "thresholds.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ThresholdRegistry.model_validate(payload)


def validate_threshold_registry(
    registry: ThresholdRegistry,
    catalog: CatalogSnapshot,
    window_registry: ParameterFamilyRegistry,
) -> list[str]:
    errors: list[str] = []
    counts = Counter(family.family_id for family in registry.families)
    for family_id, count in sorted(counts.items()):
        if count > 1:
            errors.append(f"duplicate threshold family id: {family_id}")

    classifications = {
        row.factor_id: row
        for row in classify_factor_parameters(catalog.factors, window_registry)
    }
    factor_ids = {factor.factor_id for factor in catalog.factors}
    source_modes: dict[tuple[str, str], str] = {}
    event_keys: dict[str, str] = {}
    for family in registry.families:
        source = family.source_factor_id
        if not source or source not in factor_ids:
            errors.append(f"missing source factor: {source}")
            continue
        status = classifications[source].status
        if status == "unclassified":
            errors.append(f"unclassified source factor: {source}")
        if family.kind == "threshold":
            if status != "fixed":
                errors.append(f"threshold source must be fixed: {source}")
            assert family.mode is not None
            key = (source, family.mode)
            previous = source_modes.get(key)
            if previous is not None:
                errors.append(f"duplicate threshold source/mode: {source}/{family.mode}")
            else:
                source_modes[key] = family.family_id
        else:
            if status == "blocked":
                errors.append(f"blocked event source factor: {source}")
            assert family.event_key is not None
            previous = event_keys.get(family.event_key)
            if previous is not None:
                errors.append(f"duplicate event holding key: {family.event_key}")
            else:
                event_keys[family.event_key] = family.family_id
    return errors
