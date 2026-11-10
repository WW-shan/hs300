"""Apply persisted data audits to research pools and watch versions."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.factor_research.catalog import build_catalog, load_latest_catalog, save_catalog
from app.factor_research.fingerprints import hash_token, sha256_file, sha256_json
from app.factor_research.ledger import ResearchLedger
from app.factor_research.models import (
    FactorAuditRecord,
    FactorResearchModel,
    FactorSetVersion,
    PoolMember,
    SetHead,
)
from app.factor_research.pools import PoolStore
from app.factor_research.references import ReferenceResolver
from app.factor_research.sets import FactorSetStore
from app.factors import store

POOL_IDS = (
    "core_v1",
    "financial_v1",
    "sentiment_flow_v1",
    "industry_v1",
    "regime_v1",
)


class AuditApplicationResult(FactorResearchModel):
    audit_id: str
    catalog_snapshot_id: str
    pool_revisions: dict[str, int]
    core_version: int | None
    ledger_records: int


def select_pool_candidates(
    factors: Mapping[str, FactorAuditRecord],
    findings: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[FactorAuditRecord]]:
    """Group integrity-passing factors without making efficacy claims."""
    selected: dict[str, list[FactorAuditRecord]] = {pool_id: [] for pool_id in POOL_IDS}
    for factor_id in sorted(factors):
        item = factors[factor_id]
        if not item.available or findings.get(factor_id, {}).get("integrity") != "pass":
            continue
        if item.cross_section_type == "market_level":
            selected["regime_v1"].append(item)
        elif item.data_class == "financial":
            selected["financial_v1"].append(item)
        elif item.data_class == "industry":
            selected["industry_v1"].append(item)
        elif item.data_class in {"flow", "sentiment"}:
            selected["sentiment_flow_v1"].append(item)
        elif item.data_class == "price":
            selected["core_v1"].append(item)
    return selected


def _load_audit_bundle(data_dir: Path, audit_id: str) -> tuple[dict[str, Any], dict[str, FactorAuditRecord], dict[str, dict[str, Any]], Path]:
    root = data_dir / "factor_research" / "audits" / hash_token(audit_id)
    manifest_path = root / "manifest.json"
    metrics_path = root / "metrics.json"
    findings_path = root / "findings.json"
    for path in (manifest_path, metrics_path, findings_path):
        if not path.exists():
            raise FileNotFoundError(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    if manifest.get("audit_id") != audit_id or metrics.get("audit_id") != audit_id:
        raise ValueError("audit identity mismatch")
    factors = {
        str(factor_id): FactorAuditRecord.model_validate(payload)
        for factor_id, payload in metrics.get("factors", {}).items()
    }
    if int(manifest.get("factor_count", -1)) != len(factors):
        raise ValueError("audit manifest factor_count mismatch")
    return manifest, factors, findings, metrics_path


def _head_token(head: SetHead | None) -> str | None:
    if head is None:
        return None
    payload = head.model_dump(mode="json")
    payload.pop("updated_at", None)
    payload.pop("ledger_record_id", None)
    return sha256_json(payload)


def _resolved_member(resolver: ReferenceResolver, item: Mapping[str, Any]) -> dict[str, Any]:
    factor_id = str(item["factor_id"])
    resolved = resolver.resolve(factor_id)
    if resolved.reference_kind == "dangling_reference" or not resolved.definition_fingerprint:
        raise ValueError(f"audit candidate cannot be resolved: {factor_id}")
    raw = dict(item)
    raw["factor_id"] = factor_id
    raw["factor_version"] = resolved.factor_version
    raw["definition_fingerprint"] = resolved.definition_fingerprint
    raw["reference_kind"] = resolved.reference_kind
    return raw


def _refresh_member(resolver: ReferenceResolver, member: PoolMember) -> dict[str, Any]:
    return _resolved_member(resolver, member.model_dump(mode="json"))


def _pool_signature(members: list[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            str(item["factor_id"]),
            item.get("factor_version"),
            str(item["definition_fingerprint"]),
            str(item.get("role", "alpha")),
        )
        for item in members
    )


def _set_member_signature(version: FactorSetVersion) -> list[tuple[Any, ...]]:
    return sorted(
        (
            item.factor_id,
            item.factor_version,
            item.definition_fingerprint,
            item.role,
            item.membership_status,
            item.direction,
            item.weight,
        )
        for item in version.members
    )


def _updated_set_members(
    resolver: ReferenceResolver,
    version: FactorSetVersion,
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for member in version.members:
        raw = _resolved_member(resolver, member.model_dump(mode="json"))
        members.append(raw)
    return members


def _candidate_member(
    resolver: ReferenceResolver,
    item: FactorAuditRecord,
    audit_id: str,
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    role = "conditioner" if item.cross_section_type == "market_level" else "alpha"
    return _resolved_member(resolver, {
        "factor_id": item.factor_id,
        "role": role,
        "reason": (
            f"audit {audit_id}: integrity=pass; data_class={item.data_class}; "
            f"cross_section={item.cross_section_type}; valid_dates={item.valid_dates}"
        ),
        "evidence_refs": evidence_refs,
    })


def apply_factor_audit(
    data_dir: Path,
    audit_id: str,
    *,
    actor: str = "research",
) -> AuditApplicationResult:
    """Apply one audit to pools and refresh core_v1 as a watch-only version."""
    data_dir = Path(data_dir)
    manifest, factors, findings, metrics_path = _load_audit_bundle(data_dir, audit_id)
    catalog = load_latest_catalog(data_dir)
    if catalog is None:
        catalog = build_catalog(data_dir)
        save_catalog(data_dir, catalog)
    ledger = ResearchLedger(data_dir)
    audit_record = next(
        (
            record for record in ledger.records()
            if record.operation == "factor_data_audit_published"
            and record.input_fingerprints.get("audit_id") == audit_id
        ),
        None,
    )
    if audit_record is None:
        integrity_counts: dict[str, int] = {}
        for finding in findings.values():
            key = str(finding.get("integrity", "unknown"))
            integrity_counts[key] = integrity_counts.get(key, 0) + 1
        ledger.append(
            "factor_data_audit_published",
            actor=actor,
            payload={
                "catalog_snapshot_id": catalog.snapshot_id,
                "input_fingerprints": {
                    "audit_id": audit_id,
                    "metrics_sha256": sha256_file(metrics_path),
                },
                "evidence_refs": [{
                    "kind": "factor_audit",
                    "audit_id": audit_id,
                    "path": str(metrics_path.relative_to(data_dir)),
                }],
                "metrics": {
                    "factor_count": len(factors),
                    "integrity_counts": integrity_counts,
                    "generated_at": manifest.get("generated_at"),
                },
                "verdict": "watch_only",
            },
        )

    store.load_into_registry(data_dir)
    resolver = ReferenceResolver.from_current(data_dir)
    pools = PoolStore(data_dir, resolver=resolver, ledger=ledger)
    selected = select_pool_candidates(factors, findings)
    audit_ref = {
        "kind": "factor_audit",
        "audit_id": audit_id,
        "path": str(metrics_path.relative_to(data_dir)),
        "sha256": sha256_file(metrics_path),
    }
    pool_revisions: dict[str, int] = {}
    for pool_id in POOL_IDS:
        current = pools.get(pool_id)
        current_members = list(current.members) if current is not None else []
        by_factor = {item.factor_id: _refresh_member(resolver, item) for item in current_members}
        for item in selected[pool_id]:
            if item.factor_id not in by_factor:
                by_factor[item.factor_id] = _candidate_member(resolver, item, audit_id, [audit_ref])
        members = [by_factor[key] for key in sorted(by_factor)]
        if current is not None and _pool_signature(members) == _pool_signature(
            [item.model_dump(mode="json") for item in current.members]
        ):
            pool_revisions[pool_id] = current.revision
            continue
        revision = pools.create_revision(
            pool_id,
            members,
            current.revision if current is not None else 0,
            f"应用数据审计 {audit_id} 的完整性候选",
            label=pool_id,
            purpose=current.purpose if current is not None else "",
            actor=actor,
        )
        pool_revisions[pool_id] = revision.revision

    sets = FactorSetStore(data_dir, resolver=resolver, ledger=ledger, catalog=catalog)
    head = sets.head("core_v1")
    core_version: int | None = None
    if head is not None:
        if head.state == "active":
            raise ValueError("refusing to refresh an active core_v1 head automatically")
        source_version_number = head.watch_version or head.paused_version
        if source_version_number is None:
            raise ValueError("core_v1 head has no watch/paused version")
        source_version = sets.load_version("core_v1", source_version_number)
        updated_members = _updated_set_members(resolver, source_version)
        current_signature = _set_member_signature(source_version)
        updated_signature = sorted(
            (
                str(item["factor_id"]),
                item.get("factor_version"),
                str(item["definition_fingerprint"]),
                str(item.get("role", "alpha")),
                str(item.get("membership_status", "watch")),
                str(item.get("direction", "none")),
                item.get("weight"),
            )
            for item in updated_members
        )
        if current_signature == updated_signature and source_version.catalog_snapshot_id == catalog.snapshot_id:
            core_version = source_version.version
        else:
            expected_head = _head_token(head)
            version = sets.create_version(
                "core_v1",
                updated_members,
                source_version.version,
                expected_head,
                f"数据审计 {audit_id} 更新定义指纹（watch only）",
                actor=actor,
                scope=source_version.scope,
            )
            sets.set_head(
                "core_v1",
                "watch",
                version.version,
                expected_head,
                "taxonomy 更新只刷新定义指纹，不改变有效性结论",
                actor=actor,
            )
            core_version = version.version

    return AuditApplicationResult(
        audit_id=audit_id,
        catalog_snapshot_id=catalog.snapshot_id,
        pool_revisions=pool_revisions,
        core_version=core_version,
        ledger_records=len(ledger.records()),
    )


def ensure_watch_factor_set(
    data_dir: Path,
    set_id: str,
    members: list[Mapping[str, Any]],
    change_reason: str,
    *,
    actor: str = "research",
) -> FactorSetVersion:
    """Create or refresh a watch-only factor set without ever activating it."""
    data_dir = Path(data_dir)
    store.load_into_registry(data_dir)
    catalog = load_latest_catalog(data_dir)
    if catalog is None:
        catalog = build_catalog(data_dir)
        save_catalog(data_dir, catalog)
    resolver = ReferenceResolver.from_current(data_dir)
    ledger = ResearchLedger(data_dir)
    sets = FactorSetStore(data_dir, resolver=resolver, ledger=ledger, catalog=catalog)
    head = sets.head(set_id)
    if head is not None and head.state == "active":
        raise ValueError(f"refusing to replace active set {set_id} with a watch version")

    proposed: list[dict[str, Any]] = []
    for item in members:
        raw = {
            **dict(item),
            "role": str(item.get("role", "alpha")),
            "membership_status": "watch",
        }
        proposed.append(_resolved_member(resolver, raw))
    proposed_signature = sorted(
        (
            str(item["factor_id"]),
            item.get("factor_version"),
            str(item["definition_fingerprint"]),
            str(item.get("role", "alpha")),
            str(item.get("membership_status", "watch")),
            str(item.get("direction", "none")),
            item.get("weight"),
        )
        for item in proposed
    )
    if head is not None and head.watch_version is not None:
        current = sets.load_version(set_id, head.watch_version)
        if current.catalog_snapshot_id == catalog.snapshot_id and _set_member_signature(current) == proposed_signature:
            return current

    expected_head = _head_token(head)
    version = sets.create_version(
        set_id,
        proposed,
        head.watch_version if head is not None else None,
        expected_head,
        change_reason,
        actor=actor,
        scope={"asset_type": "stock", "universe": "hs300_current"},
    )
    sets.set_head(
        set_id,
        "watch",
        version.version,
        expected_head,
        "研究候选只进入 watch，不自动激活",
        actor=actor,
    )
    return version
