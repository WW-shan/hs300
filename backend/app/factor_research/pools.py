"""Versioned research-pool revisions."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import content_hash, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import FileLock, atomic_write_json
from app.factor_research.models import PoolHead, PoolMember, PoolRevision
from app.factor_research.references import ReferenceResolver


class PoolConflictError(RuntimeError):
    """The caller's expected pool revision is no longer current."""


_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_ALLOWED_PHYSICAL_ROLES = {"neutralizer", "risk_filter", "diagnostic"}


class PoolStore:
    def __init__(
        self,
        data_dir: Path,
        resolver: ReferenceResolver | None = None,
        ledger: ResearchLedger | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "factor_research"
        self.resolver = resolver or ReferenceResolver.from_current(self.data_dir)
        self.ledger = ledger or ResearchLedger(self.data_dir)
        self.lock_path = self.root / ".pool.lock"

    def _root(self, pool_id: str) -> Path:
        if not _ID_RE.match(pool_id):
            raise ValueError(f"invalid pool_id: {pool_id!r}")
        return self.root / "pools" / pool_id

    def _revision_path(self, pool_id: str, revision: int) -> Path:
        return self._root(pool_id) / "revisions" / f"r{revision:04d}.json"

    def _head_path(self, pool_id: str) -> Path:
        return self._root(pool_id) / "head.json"

    def _load_revision(self, path: Path) -> PoolRevision:
        payload = json.loads(path.read_text(encoding="utf-8"))
        revision = PoolRevision.model_validate(payload)
        expected = content_hash(revision.model_dump(mode="json"), "content_sha256")
        if expected != revision.content_sha256:
            raise ValueError(f"pool revision hash mismatch: {path}")
        return revision

    def _head_from_ledger(self, pool_id: str) -> PoolHead | None:
        records = self.ledger.records()
        relevant = [
            record for record in records
            if record.operation == "pool_revision_created" and record.pool_id == pool_id
        ]
        if not relevant:
            return None
        record = relevant[-1]
        if record.pool_revision is None:
            raise ValueError(f"pool ledger record missing revision: {record.record_id}")
        path = self._revision_path(pool_id, record.pool_revision)
        revision = self._load_revision(path)
        return PoolHead(
            pool_id=pool_id,
            revision=revision.revision,
            revision_sha256=revision.content_sha256,
            updated_at=record.recorded_at,
            ledger_record_id=record.record_id,
        )

    def _normalise_member(self, item: PoolMember | Mapping[str, Any], now: str) -> PoolMember:
        raw = item.model_dump(mode="json") if isinstance(item, PoolMember) else dict(item)
        factor_id = str(raw.get("factor_id", ""))
        if not factor_id:
            raise ValueError("pool member factor_id is required")
        resolved = self.resolver.resolve(factor_id)
        if resolved.reference_kind == "dangling_reference":
            raise ValueError(f"pool member is dangling: {factor_id}")
        role = str(raw.get("role", "alpha"))
        if resolved.reference_kind == "physical_column" and role not in _ALLOWED_PHYSICAL_ROLES:
            raise ValueError(f"physical column {factor_id} cannot have role {role}")
        if role == "alpha" and resolved.reference_kind != "registered_factor":
            raise ValueError(f"alpha member must be a registered factor: {factor_id}")
        reference_kind = str(raw.get("reference_kind", resolved.reference_kind))
        if reference_kind != resolved.reference_kind:
            raise ValueError(f"reference_kind mismatch for {factor_id}")
        fingerprint = str(raw.get("definition_fingerprint") or resolved.definition_fingerprint or "")
        if not fingerprint:
            raise ValueError(f"definition_fingerprint unavailable for {factor_id}")
        if resolved.definition_fingerprint and fingerprint != resolved.definition_fingerprint:
            raise ValueError(f"definition_fingerprint mismatch for {factor_id}")
        factor_version = raw.get("factor_version", resolved.factor_version)
        if resolved.factor_version is not None and factor_version not in {None, resolved.factor_version}:
            raise ValueError(f"factor_version mismatch for {factor_id}")
        return PoolMember(
            factor_id=factor_id,
            factor_version=resolved.factor_version if resolved.factor_version is not None else factor_version,
            definition_fingerprint=fingerprint,
            reference_kind=reference_kind,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            added_at=str(raw.get("added_at") or now),
            reason=str(raw.get("reason") or raw.get("rationale") or ""),
            evidence_refs=list(raw.get("evidence_refs") or []),
        )

    def create_revision(
        self,
        pool_id: str,
        members: Iterable[PoolMember | Mapping[str, Any]],
        expected_revision: int,
        change_reason: str,
        *,
        label: str | None = None,
        purpose: str = "",
        actor: str = "research",
    ) -> PoolRevision:
        self._root(pool_id)
        with FileLock(self.lock_path):
            head = self._head_from_ledger(pool_id)
            current = head.revision if head is not None else 0
            if int(expected_revision) != current:
                raise PoolConflictError(
                    f"pool {pool_id} expected revision {expected_revision}, current {current}"
                )
            now = utc_now()
            normalised: list[PoolMember] = []
            seen: set[str] = set()
            for item in members:
                member = self._normalise_member(item, now)
                if member.factor_id in seen:
                    raise ValueError(f"duplicate pool member: {member.factor_id}")
                seen.add(member.factor_id)
                normalised.append(member)

            revision_number = current + 1
            payload: dict[str, Any] = {
                "schema_version": 1,
                "pool_id": pool_id,
                "revision": revision_number,
                "label": label or pool_id,
                "purpose": purpose,
                "not_an_efficacy_claim": True,
                "members": [item.model_dump(mode="json") for item in normalised],
                "created_at": now,
                "parent_revision": current or None,
                "change_reason": change_reason,
            }
            revision_sha = content_hash(payload, "content_sha256")
            revision = PoolRevision.model_validate({**payload, "content_sha256": revision_sha})
            path = self._revision_path(pool_id, revision_number)
            if path.exists():
                raise PoolConflictError(f"pool revision already exists: {path}")
            atomic_write_json(path, revision)
            record = self.ledger.append(
                "pool_revision_created",
                actor=actor,
                payload={
                    "pool_id": pool_id,
                    "pool_revision": revision_number,
                    "reason": change_reason,
                    "input_fingerprints": {"revision_sha256": revision_sha},
                },
            )
            atomic_write_json(self._head_path(pool_id), PoolHead(
                pool_id=pool_id,
                revision=revision_number,
                revision_sha256=revision_sha,
                updated_at=now,
                ledger_record_id=record.record_id,
            ))
            return revision

    def get(self, pool_id: str, revision: int | None = None) -> PoolRevision | None:
        self._root(pool_id)
        if revision is None:
            head = self._head_from_ledger(pool_id)
            if head is None:
                path = self._head_path(pool_id)
                if not path.exists():
                    return None
                head = PoolHead.model_validate(json.loads(path.read_text(encoding="utf-8")))
            revision = head.revision
        path = self._revision_path(pool_id, revision)
        if not path.exists():
            return None
        return self._load_revision(path)

    def list_heads(self) -> list[PoolHead]:
        pools_root = self.root / "pools"
        if not pools_root.exists():
            return []
        out: list[PoolHead] = []
        for path in sorted(pools_root.iterdir()):
            if not path.is_dir():
                continue
            head = self._head_from_ledger(path.name)
            if head is None and (path / "head.json").exists():
                head = PoolHead.model_validate(json.loads((path / "head.json").read_text(encoding="utf-8")))
            if head is not None:
                out.append(head)
        return out
