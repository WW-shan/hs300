"""Immutable factor mother-set snapshots."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import (
    canonical_json,
    content_hash,
    hash_token,
    sha256_file,
    sha256_json,
    utc_now,
)
from app.factor_research.locking import atomic_write_json
from app.factor_research.models import (
    CatalogSnapshot,
    DanglingReferenceRecord,
    FactorComponent,
    FactorRecord,
    HistoricalDefinitionRecord,
    PhysicalColumnRecord,
    VirtualScoringFieldRecord,
)
from app.factor_research.references import (
    ReferenceResolver,
    load_field_taxonomy,
    load_historical_aliases,
)
from app.factors import store
from app.factors.registry import FactorSpec, all_factors, virtual_dependencies


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _dynamic_sources(data_dir: Path) -> tuple[dict[str, tuple[str, str, str | None]], list[str]]:
    """Return dynamic custom/composite id -> (kind, file hash, status), plus errors."""
    root = data_dir / "user_data" / "custom_factors"
    out: dict[str, tuple[str, str, str | None]] = {}
    errors: list[str] = []
    if not root.exists():
        return out, errors
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
            errors.append(f"{path.name}: missing id")
            continue
        out[str(payload["id"])] = (
            str(payload.get("kind", "custom")),
            sha256_file(path),
            str(payload.get("status", "draft")),
        )
    return out, errors


def _extension_sources(data_dir: Path) -> tuple[dict[str, tuple[str, str, str, bool]], list[str]]:
    """Map generated ext factor ids to (config_id, field_name, mode, market_level)."""
    out: dict[str, tuple[str, str, str, bool]] = {}
    errors: list[str] = []
    try:
        from app.factors.ext_factors import ext_column_name
        from app.services.ext_data import ExtConfigStore

        configs = ExtConfigStore(data_dir).load_all()
    except Exception as exc:  # noqa: BLE001 - extension config is an input boundary
        return out, [f"extension config: {exc}"]
    for config in configs:
        try:
            for field in config.fields:
                if field.dtype not in {"int", "float"}:
                    continue
                out[ext_column_name(config.id, field.name)] = (
                    config.id,
                    field.name,
                    config.mode,
                    bool(config.market_level),
                )
        except Exception as exc:  # noqa: BLE001 - malformed config is isolated
            errors.append(f"extension config {getattr(config, 'id', '?')}: {exc}")
    return out, errors


def _load_dynamic(data_dir: Path) -> list[str]:
    """Load custom/composite factors if the process registry does not have them yet."""
    dynamic = [spec for spec in all_factors(asset_type=None) if spec.kind in {"custom", "composite"}]
    root = data_dir / "user_data" / "custom_factors"
    if dynamic or not root.exists() or not any(root.glob("*.json")):
        return []
    try:
        return store.load_into_registry(data_dir)
    except Exception as exc:  # noqa: BLE001 - load errors become source_completeness=partial
        return [f"dynamic factor load: {exc}"]


def _data_class(spec: FactorSpec, taxonomy: Mapping[str, Any] | None, market_level: bool) -> str:
    if taxonomy and taxonomy.get("data_class") in {
        "price", "sentiment", "flow", "industry", "macro", "financial"
    }:
        return str(taxonomy["data_class"])
    if spec.group == "财务":
        return "financial"
    if spec.id.startswith("ext_"):
        return "macro" if market_level else "unknown"
    return "price"


def _pit_state(spec: FactorSpec, data_class: str, *, source_type: str, mode: str | None) -> tuple[str, str, str]:
    if spec.pit:
        pit_status = "declared"
    elif data_class == "financial":
        pit_status = "unknown"
    elif data_class in {"price", "industry"} and source_type == "builtin":
        pit_status = "not_applicable"
    else:
        pit_status = "unknown"

    if (source_type == "extension" and mode == "snapshot") or (data_class == "financial" and not spec.pit) or source_type in {"custom", "composite"}:
        future = "unknown"
    else:
        future = "clear"
    return pit_status, "financial_announce" if spec.pit else "none", future


def _source_and_hash(
    spec: FactorSpec,
    data_dir: Path,
    extension_sources: Mapping[str, tuple[str, str, str, bool]],
    dynamic_sources: Mapping[str, tuple[str, str, str | None]],
) -> tuple[dict[str, Any], str | None, str | None, bool]:
    if spec.id in extension_sources:
        config_id, field, mode, market_level = extension_sources[spec.id]
        config_path = data_dir / "ext_data" / config_id / "config.json"
        config_hash = sha256_file(config_path) if config_path.exists() else None
        source = {
            "type": "extension",
            "config_id": config_id,
            "field": field,
            "mode": mode,
        }
        return source, config_hash, mode, market_level

    if spec.id in dynamic_sources:
        kind, file_hash, status = dynamic_sources[spec.id]
        return {
            "type": kind if kind in {"custom", "composite"} else "custom",
            "factor_id": spec.id,
            "file_sha256": file_hash,
            "status": status,
        }, file_hash, None, False

    return {
        "type": "builtin",
        "module": "factors.registry",
        "catalog": "_CATALOG",
    }, None, None, False


def build_catalog(
    data_dir: Path,
    *,
    generated_at: str | None = None,
    extra_references: Iterable[str] = (),
) -> CatalogSnapshot:
    """Build a deterministic snapshot from the current registry and metadata.

    ``generated_at`` is intentionally excluded from the snapshot hash. The
    same definitions and source contracts therefore produce the same id even
    when the audit is run on another day.
    """
    data_dir = Path(data_dir)
    load_errors = _load_dynamic(data_dir)
    extension_sources, ext_errors = _extension_sources(data_dir)
    load_errors.extend(ext_errors)
    dynamic_sources, custom_errors = _dynamic_sources(data_dir)
    load_errors.extend(custom_errors)
    taxonomy = load_field_taxonomy(data_dir)
    aliases = load_historical_aliases(data_dir)

    specs = all_factors(asset_type=None)
    resolver = ReferenceResolver(
        specs=specs,
        historical_aliases=aliases,
        taxonomy=taxonomy,
    )

    factors: list[FactorRecord] = []
    for spec in specs:
        source, source_hash, mode, market_level = _source_and_hash(
            spec, data_dir, extension_sources, dynamic_sources
        )
        resolved = resolver.resolve(spec.id)
        data_class = _data_class(spec, taxonomy.get(spec.id), market_level)
        pit_status, pit_source, future = _pit_state(
            spec,
            data_class,
            source_type=str(source["type"]),
            mode=mode,
        )
        definition_fp = sha256_json({
            "resolver_fingerprint": resolved.definition_fingerprint,
            "source": source,
            "source_hash": source_hash,
            "pit_status": pit_status,
            "future_data_status": future,
            "data_class": data_class,
        })
        factors.append(FactorRecord(
            factor_id=spec.id,
            factor_version=spec.version,
            label=spec.label,
            group=spec.group,
            kind=spec.kind,
            formula_text=spec.formula_text,
            dependencies=sorted(spec.dependencies),
            components=[FactorComponent(factor_id=member, weight=weight) for member, weight in spec.components],
            direction=spec.direction,
            unit=spec.unit,
            warmup_bars=spec.warmup_bars,
            pit_status=pit_status,  # type: ignore[arg-type]
            pit_source=pit_source,
            future_data_status=future,  # type: ignore[arg-type]
            asset_types=sorted(spec.asset_types),
            scale_free=spec.scale_free,
            incremental_safe=spec.incremental_safe,
            definition_stability=spec.stability,
            definition_status=(
                "retired" if dynamic_sources.get(spec.id, ("", "", None))[2] == "retired" else "available"
            ),
            source=source,  # type: ignore[arg-type]
            data_class=data_class,  # type: ignore[arg-type]
            cross_section_type="market_level" if market_level else "unknown",
            definition_fingerprint=definition_fp,
            reference_kind="registered_factor",
            tags=list(spec.tags),
        ))

    physical_columns = [
        PhysicalColumnRecord(
            column_id=column,
            data_class=(
                "price" if column in {
                    "open", "high", "low", "close", "volume", "amount",
                    "turnover_rate", "prev_close", "raw_close", "raw_high", "raw_low",
                } else "unknown"
            ),
            factor_eligible=False,
            source="enriched",
            definition_fingerprint=sha256_json({"kind": "physical_column", "column_id": column}),
        )
        for column in sorted(resolver.physical_columns)
    ]

    virtual_fields = []
    for field_id, deps in sorted(virtual_dependencies().items()):
        virtual_fields.append(VirtualScoringFieldRecord(
            field_id=field_id,
            registered_factor_id=field_id if any(spec.id == field_id for spec in specs) else None,
            dependencies=sorted(deps),
            factor_eligible=any(spec.id == field_id for spec in specs),
            definition_fingerprint=sha256_json({"kind": "virtual_scoring_field", "id": field_id, "dependencies": sorted(deps)}),
        ))

    historical_definitions = [
        HistoricalDefinitionRecord(
            alias_id=alias.alias_id,
            replacement_factor_id=alias.replacement_factor_id,
            evidence_refs=alias.evidence_refs,
            note=alias.note,
        )
        for alias in aliases
    ]

    dangling: list[DanglingReferenceRecord] = []
    for reference_id in sorted(set(str(item) for item in extra_references)):
        resolved = resolver.resolve(reference_id)
        if resolved.reference_kind == "dangling_reference":
            dangling.append(DanglingReferenceRecord(
                reference_id=reference_id,
                reason=str(resolved.details.get("reason", "unresolved reference")),
            ))

    factor_payload = [item.model_dump(mode="json") for item in factors]
    registry_fingerprint = sha256_json([
        (item["factor_id"], item["factor_version"], item["definition_fingerprint"])
        for item in factor_payload
    ])
    base_payload: dict[str, Any] = {
        "schema_version": 1,
        "registry_fingerprint": registry_fingerprint,
        "source_completeness": "partial" if load_errors else "complete",
        "factor_count": len(factors),
        "factors": factor_payload,
        "physical_columns": [item.model_dump(mode="json") for item in physical_columns],
        "virtual_scoring_fields": [item.model_dump(mode="json") for item in virtual_fields],
        "historical_definitions": [item.model_dump(mode="json") for item in historical_definitions],
        "dangling_references": [item.model_dump(mode="json") for item in dangling],
        "load_errors": load_errors,
    }
    snapshot_id = content_hash(base_payload)
    return CatalogSnapshot(
        **base_payload,
        snapshot_id=snapshot_id,
        generated_at=generated_at or utc_now(),
    )


def _snapshot_path(data_dir: Path, snapshot_id: str) -> Path:
    return Path(data_dir) / "factor_research" / "catalog" / "snapshots" / f"{hash_token(snapshot_id)}.json"


def save_catalog(data_dir: Path, snapshot: CatalogSnapshot) -> Path:
    path = _snapshot_path(data_dir, snapshot.snapshot_id)
    if path.exists():
        existing = CatalogSnapshot.model_validate(_read_json(path))
        left = existing.model_dump(mode="json")
        right = snapshot.model_dump(mode="json")
        # generated_at is intentionally outside the immutable content hash.
        left.pop("generated_at", None)
        right.pop("generated_at", None)
        if canonical_json(left) != canonical_json(right):
            # The same content hash may not be reused for different content.
            raise ValueError(f"catalog snapshot collision: {snapshot.snapshot_id}")
        stored = existing
    else:
        atomic_write_json(path, snapshot)
        stored = snapshot
    latest_path = Path(data_dir) / "factor_research" / "catalog" / "latest.json"
    atomic_write_json(latest_path, {
        "schema_version": 1,
        "snapshot_id": stored.snapshot_id,
        "registry_fingerprint": stored.registry_fingerprint,
        "classification_ref": None,
        "source_completeness": stored.source_completeness,
        "generated_at": stored.generated_at,
        "factor_count": stored.factor_count,
        "ledger_record_id": None,
    })
    return path


def load_catalog(data_dir: Path, snapshot_id: str) -> CatalogSnapshot:
    path = _snapshot_path(data_dir, snapshot_id)
    snapshot = CatalogSnapshot.model_validate(_read_json(path))
    payload = snapshot.model_dump(mode="json")
    payload.pop("snapshot_id", None)
    payload.pop("generated_at", None)
    if content_hash(payload) != snapshot.snapshot_id:
        raise ValueError(f"catalog snapshot hash mismatch: {snapshot.snapshot_id}")
    return snapshot


def load_latest_catalog(data_dir: Path) -> CatalogSnapshot | None:
    path = Path(data_dir) / "factor_research" / "catalog" / "latest.json"
    if not path.exists():
        return None
    pointer = _read_json(path)
    snapshot_id = pointer.get("snapshot_id") if isinstance(pointer, Mapping) else None
    if not isinstance(snapshot_id, str):
        raise ValueError("catalog latest pointer has no snapshot_id")
    return load_catalog(data_dir, snapshot_id)
