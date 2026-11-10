"""Resolve heterogeneous strategy/factor references without name guessing."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import sha256_json
from app.factor_research.models import (
    DataClass,
    HistoricalDefinitionRecord,
    ResolvedReference,
    SourceLocator,
)
from app.factors.dsl import BASE_COLUMNS
from app.factors.registry import FactorSpec, all_factors, virtual_dependencies


def _valid_data_class(value: Any) -> DataClass:
    if value in {"price", "sentiment", "flow", "industry", "macro", "financial"}:
        return value  # type: ignore[return-value]
    return "unknown"


def load_field_taxonomy(data_dir: Path | None) -> dict[str, dict[str, Any]]:
    if data_dir is None:
        return {}
    path = Path(data_dir) / "factor_research" / "catalog" / "field_taxonomy.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = payload.get("records", []) if isinstance(payload, Mapping) else []
    out: dict[str, dict[str, Any]] = {}
    for item in records if isinstance(records, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("factor_id"), str):
            out[str(item["factor_id"])] = dict(item)
    return out


def load_historical_aliases(data_dir: Path | None) -> list[HistoricalDefinitionRecord]:
    if data_dir is None:
        return []
    path = Path(data_dir) / "factor_research" / "catalog" / "historical_aliases.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = payload.get("aliases", []) if isinstance(payload, Mapping) else payload
    out: list[HistoricalDefinitionRecord] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        alias_id = item.get("id", item.get("alias_id"))
        evidence = item.get("evidence_refs") or []
        if not isinstance(alias_id, str) or not evidence:
            continue
        out.append(HistoricalDefinitionRecord(
            alias_id=alias_id,
            replacement_factor_id=item.get("replacement_factor_id"),
            evidence_refs=list(evidence),
            note=item.get("note"),
        ))
    return out


def _factor_fingerprint(spec: FactorSpec, taxonomy: Mapping[str, Any] | None) -> str:
    payload = {
        "kind": spec.kind,
        "version": spec.version,
        "formula_text": spec.formula_text,
        "dependencies": sorted(spec.dependencies),
        "components": [list(item) for item in spec.components],
        "warmup_bars": spec.warmup_bars,
        "asset_types": sorted(spec.asset_types),
        "scale_free": spec.scale_free,
        "incremental_safe": spec.incremental_safe,
        "pit": spec.pit,
        "pit_source": spec.pit_source,
        "taxonomy": dict(taxonomy or {}),
    }
    return sha256_json(payload)


def _source_for(spec: FactorSpec) -> SourceLocator:
    if spec.kind in {"custom", "composite"}:
        return SourceLocator(type=spec.kind, factor_id=spec.id)  # type: ignore[arg-type]
    if spec.id.startswith("ext_"):
        return SourceLocator(type="extension", field=spec.id)
    return SourceLocator(type="builtin", module="factors.registry", catalog="_CATALOG")


class ReferenceResolver:
    """Exact-name resolver for factors, virtual fields and physical columns."""

    def __init__(
        self,
        *,
        specs: Iterable[FactorSpec],
        physical_columns: Iterable[str] | None = None,
        virtual_fields: Iterable[str] | None = None,
        signal_fields: Iterable[str] | None = None,
        historical_aliases: Iterable[HistoricalDefinitionRecord] | None = None,
        taxonomy: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.specs = {spec.id: spec for spec in specs}
        if physical_columns is None:
            from app.indicators.pipeline import ENRICHED_COLUMNS

            physical_columns = [*BASE_COLUMNS, *ENRICHED_COLUMNS.keys()]
        self.physical_columns = frozenset(str(item) for item in physical_columns)
        self.virtual_fields = frozenset(str(item) for item in (virtual_fields or virtual_dependencies().keys()))
        self.signal_fields = frozenset(str(item) for item in (signal_fields or ()))
        self.historical_aliases = {item.alias_id: item for item in (historical_aliases or ())}
        self.taxonomy = {str(key): dict(value) for key, value in (taxonomy or {}).items()}

    @classmethod
    def from_current(cls, data_dir: Path | None = None) -> ReferenceResolver:
        if data_dir is not None:
            try:
                from app.factors.ext_factors import ensure_synced

                ensure_synced(data_dir)
            except Exception:  # noqa: BLE001 - source completeness is recorded by catalog
                pass
        specs = all_factors(asset_type=None)
        signal_fields: Iterable[str] = ()
        if data_dir is not None:
            try:
                from app.factors.ext_factors import ext_string_fields

                signal_fields = ext_string_fields(data_dir)
            except Exception:  # noqa: BLE001 - optional signal metadata is isolated
                signal_fields = ()
        return cls(
            specs=specs,
            signal_fields=signal_fields,
            historical_aliases=load_historical_aliases(data_dir),
            taxonomy=load_field_taxonomy(data_dir),
        )

    def _taxonomy_for(self, reference_id: str) -> dict[str, Any]:
        return dict(self.taxonomy.get(reference_id, {}))

    def resolve(self, reference_id: str) -> ResolvedReference:
        reference_id = str(reference_id)
        spec = self.specs.get(reference_id)
        if spec is not None:
            taxonomy = self._taxonomy_for(reference_id)
            if taxonomy.get("data_class"):
                data_class = _valid_data_class(taxonomy["data_class"])
            elif spec.group == "财务":
                data_class = "financial"
            elif spec.id.startswith("ext_"):
                data_class = "unknown"
            else:
                data_class = "price"
            pit_status = "declared" if spec.pit else (
                "unknown" if data_class == "financial" else "not_applicable"
            )
            return ResolvedReference(
                reference_id=reference_id,
                reference_kind="registered_factor",
                factor_eligible=True,
                factor_version=spec.version,
                definition_fingerprint=_factor_fingerprint(spec, taxonomy),
                data_class=data_class,
                source=_source_for(spec),
                details={
                    "kind": spec.kind,
                    "group": spec.group,
                    "pit_status": pit_status,
                    "asset_types": sorted(spec.asset_types),
                },
            )

        if reference_id in self.virtual_fields:
            return ResolvedReference(
                reference_id=reference_id,
                reference_kind="virtual_scoring_field",
                factor_eligible=False,
                data_class="price",
                details={"dependencies": sorted(virtual_dependencies().get(reference_id, ()))},
            )

        if reference_id in self.physical_columns:
            taxonomy = self._taxonomy_for(reference_id)
            return ResolvedReference(
                reference_id=reference_id,
                reference_kind="physical_column",
                factor_eligible=False,
                definition_fingerprint=sha256_json({
                    "kind": "physical_column",
                    "column_id": reference_id,
                }),
                data_class=_valid_data_class(taxonomy.get("data_class")),
                details={"dtype": taxonomy.get("dtype", "unknown")},
            )

        historical = self.historical_aliases.get(reference_id)
        if historical is not None:
            return ResolvedReference(
                reference_id=reference_id,
                reference_kind="historical_definition",
                factor_eligible=False,
                details={
                    "replacement_factor_id": historical.replacement_factor_id,
                    "evidence_refs": historical.evidence_refs,
                },
            )

        if reference_id in self.signal_fields:
            return ResolvedReference(
                reference_id=reference_id,
                reference_kind="signal_field",
                factor_eligible=False,
                data_class="unknown",
            )

        return ResolvedReference(
            reference_id=reference_id,
            reference_kind="dangling_reference",
            factor_eligible=False,
            details={"reason": "not found in registry, virtual fields, columns, history or signal fields"},
        )
