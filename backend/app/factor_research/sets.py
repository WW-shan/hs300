"""Immutable factor-set versions, heads and stale checks."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.catalog import build_catalog, load_latest_catalog, save_catalog
from app.factor_research.fingerprints import content_hash, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.locking import FileLock, atomic_write_json
from app.factor_research.models import (
    FactorSetVersion,
    SetHead,
    SetMember,
    SetPreflight,
)
from app.factor_research.references import ReferenceResolver
from app.factors.registry import get_factor


class SetConflictError(RuntimeError):
    """The caller's expected set head/version is no longer current."""


class AuditRequiredError(RuntimeError):
    """An active transition needs a complete audit verdict."""


_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_PHYSICAL_ROLES = {"risk_filter", "neutralizer", "diagnostic"}
_AuditChecker = Callable[[str, int], Mapping[str, Any] | None]


class FactorSetStore:
    def __init__(
        self,
        data_dir: Path,
        resolver: ReferenceResolver | None = None,
        ledger: ResearchLedger | None = None,
        *,
        catalog=None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "factor_research"
        self.resolver = resolver or ReferenceResolver.from_current(self.data_dir)
        self.ledger = ledger or ResearchLedger(self.data_dir)
        self._catalog = catalog
        self.lock_path = self.root / ".set.lock"

    def _root(self, set_id: str) -> Path:
        if not _ID_RE.match(set_id):
            raise ValueError(f"invalid set_id: {set_id!r}")
        return self.root / "sets" / set_id

    def _version_path(self, set_id: str, version: int) -> Path:
        return self._root(set_id) / "versions" / f"v{version:04d}.json"

    def _head_path(self, set_id: str) -> Path:
        return self._root(set_id) / "head.json"

    def _catalog_or_build(self):
        if self._catalog is not None:
            return self._catalog
        latest = load_latest_catalog(self.data_dir)
        if latest is None:
            latest = build_catalog(self.data_dir)
            save_catalog(self.data_dir, latest)
        self._catalog = latest
        return latest

    def _load_version(self, set_id: str, version: int) -> FactorSetVersion:
        path = self._version_path(set_id, version)
        item = FactorSetVersion.model_validate(json.loads(path.read_text(encoding="utf-8")))
        expected = content_hash(item.model_dump(mode="json"), "content_sha256")
        if expected != item.content_sha256:
            raise ValueError(f"factor set version hash mismatch: {path}")
        return item

    def _head_from_ledger(self, set_id: str) -> SetHead | None:
        records = self.ledger.records()
        relevant = [record for record in records if record.set_id == set_id]
        if not relevant:
            return None
        state: str | None = None
        watch_version: int | None = None
        watch_sha: str | None = None
        active_version: int | None = None
        active_sha: str | None = None
        paused_version: int | None = None
        paused_sha: str | None = None
        last_record_id: str | None = None
        updated_at: str | None = None
        for record in relevant:
            if record.operation == "set_head_changed" and record.set_version is not None:
                version = self._load_version(set_id, record.set_version)
                state = str(record.verdict or "")
                if state == "watch":
                    watch_version, watch_sha = version.version, version.content_sha256
                    active_version = active_sha = paused_version = paused_sha = None
                elif state == "active":
                    active_version, active_sha = version.version, version.content_sha256
                    paused_version = paused_sha = None
                elif state == "paused":
                    paused_version, paused_sha = version.version, version.content_sha256
                    active_version = active_sha = None
                else:
                    raise ValueError(f"unsupported set head state: {state!r}")
                last_record_id = record.record_id
                updated_at = record.recorded_at
        if state is None:
            return None
        return SetHead(
            set_id=set_id,
            state=state,  # type: ignore[arg-type]
            watch_version=watch_version,
            watch_version_sha256=watch_sha,
            active_version=active_version,
            active_version_sha256=active_sha,
            paused_version=paused_version,
            paused_version_sha256=paused_sha,
            updated_at=updated_at,
            ledger_record_id=last_record_id,
        )

    def _head_token(self, head: SetHead | None) -> str | None:
        if head is None:
            return None
        payload = head.model_dump(mode="json")
        payload.pop("updated_at", None)
        payload.pop("ledger_record_id", None)
        return sha256_json(payload)

    def _normalise_member(self, item: SetMember | Mapping[str, Any]) -> SetMember:
        raw = item.model_dump(mode="json") if isinstance(item, SetMember) else dict(item)
        factor_id = str(raw.get("factor_id", ""))
        if not factor_id:
            raise ValueError("set member factor_id is required")
        resolved = self.resolver.resolve(factor_id)
        if resolved.reference_kind == "dangling_reference":
            raise ValueError(f"set member is dangling: {factor_id}")
        role = str(raw.get("role", "alpha"))
        if role == "alpha" and resolved.reference_kind != "registered_factor":
            raise ValueError(f"alpha member must be a registered factor: {factor_id}")
        if resolved.reference_kind == "physical_column" and role not in _PHYSICAL_ROLES:
            raise ValueError(f"physical column {factor_id} cannot have role {role}")
        if resolved.reference_kind not in {"registered_factor", "physical_column"}:
            raise ValueError(f"unsupported set reference kind for {factor_id}: {resolved.reference_kind}")
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
        if resolved.reference_kind == "physical_column" and factor_version is not None:
            raise ValueError(f"physical column cannot have factor_version: {factor_id}")
        direction = str(raw.get("direction", "none"))
        if direction not in {"high", "low", "none"}:
            raise ValueError(f"invalid direction for {factor_id}: {direction}")
        if direction == "none" and role == "alpha" and resolved.factor_version is not None:
            spec = get_factor(factor_id)
            if spec is not None and spec.direction in {"high", "low"}:
                direction = spec.direction
        status = str(raw.get("membership_status", "watch"))
        if status not in {"watch", "active"}:
            raise ValueError(f"invalid membership_status for {factor_id}: {status}")
        weight = raw.get("weight")
        if weight is not None:
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                raise ValueError(f"weight must be numeric for {factor_id}")
            weight = float(weight)
            if not math.isfinite(weight) or weight < 0:
                raise ValueError(f"weight must be finite and nonnegative for {factor_id}")
        return SetMember(
            factor_id=factor_id,
            factor_version=resolved.factor_version if resolved.factor_version is not None else factor_version,
            definition_fingerprint=fingerprint,
            reference_kind=reference_kind,  # type: ignore[arg-type]
            role=role,  # type: ignore[arg-type]
            membership_status=status,  # type: ignore[arg-type]
            direction=direction,  # type: ignore[arg-type]
            weight=weight,
            rationale=str(raw.get("rationale") or ""),
            evidence_refs=list(raw.get("evidence_refs") or []),
        )

    def _validate_members(self, members: list[SetMember], *, active_head: bool = False) -> None:
        active_alpha = [
            item for item in members if item.role == "alpha" and item.membership_status == "active"
        ]
        watch_alpha = [
            item for item in members if item.role == "alpha" and item.membership_status == "watch"
        ]
        if active_head and not active_alpha:
            raise ValueError("active head requires at least one active alpha member")
        if active_alpha:
            if sum(item.weight or 0.0 for item in active_alpha) != 1.0:
                raise ValueError("active alpha weights must sum to 1.0")
            for item in active_alpha:
                if item.weight is None or item.direction not in {"high", "low"}:
                    raise ValueError(f"active alpha needs weight and direction: {item.factor_id}")
            for item in watch_alpha:
                if item.weight is not None:
                    raise ValueError(f"watch alpha in an active set must have null weight: {item.factor_id}")
        elif watch_alpha:
            if any(item.weight is None for item in watch_alpha):
                raise ValueError("watch alpha weights must be present")
            if sum(item.weight or 0.0 for item in watch_alpha) != 1.0:
                raise ValueError("watch alpha weights must sum to 1.0")

        for item in members:
            resolved = self.resolver.resolve(item.factor_id)
            if item.factor_version != resolved.factor_version and resolved.factor_version is not None:
                raise ValueError(f"member factor version is stale: {item.factor_id}")
            if resolved.reference_kind == "registered_factor":
                pit_status = str(resolved.details.get("pit_status", "unknown"))
                if active_head and resolved.data_class == "financial" and pit_status != "verified":
                    raise ValueError(f"financial member lacks verified PIT: {item.factor_id}")

    def create_version(
        self,
        set_id: str,
        members: Iterable[SetMember | Mapping[str, Any]],
        parent_version: int | None,
        expected_head_sha256: str | None,
        change_reason: str,
        *,
        actor: str = "research",
        scope: Mapping[str, str] | None = None,
    ) -> FactorSetVersion:
        self._root(set_id)
        with FileLock(self.lock_path):
            head = self._head_from_ledger(set_id)
            current = head
            if current is not None and expected_head_sha256 is None:
                raise SetConflictError("an existing set requires expected_head_sha256")
            if current is not None and expected_head_sha256 != self._head_token(current):
                raise SetConflictError("set head changed since the request was prepared")
            if current is None and expected_head_sha256 is not None:
                raise SetConflictError("set has no head but expected_head_sha256 was supplied")
            existing_versions = [
                int(record.set_version) for record in self.ledger.records()
                if record.set_id == set_id and record.operation == "set_version_created" and record.set_version is not None
            ]
            if current is None and existing_versions:
                raise SetConflictError("set already has a version but no head")
            if existing_versions:
                if parent_version is None:
                    raise ValueError("parent_version is required after the first version")
                if parent_version not in existing_versions:
                    raise ValueError(f"parent_version does not exist: {parent_version}")
            elif parent_version is not None:
                raise ValueError("first set version cannot have parent_version")
            version_number = max(existing_versions, default=0) + 1
            normalised = [self._normalise_member(item) for item in members]
            ids = [item.factor_id for item in normalised]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate set member")
            self._validate_members(normalised, active_head=False)
            catalog = self._catalog_or_build()
            preflight = SetPreflight(catalog_snapshot_id=catalog.snapshot_id)
            payload: dict[str, Any] = {
                "schema_version": 1,
                "set_id": set_id,
                "version": version_number,
                "parent_version": parent_version,
                "created_at": utc_now(),
                "created_by": actor,
                "change_reason": change_reason,
                "catalog_snapshot_id": catalog.snapshot_id,
                "scope": dict(scope or {"asset_type": "stock", "universe": "hs300_current"}),
                "members": [item.model_dump(mode="json") for item in normalised],
                "preflight": preflight.model_dump(mode="json"),
            }
            content_sha = content_hash(payload, "content_sha256")
            version = FactorSetVersion.model_validate({**payload, "content_sha256": content_sha})
            path = self._version_path(set_id, version_number)
            if path.exists():
                raise SetConflictError(f"set version already exists: {path}")
            atomic_write_json(path, version)
            self.ledger.append(
                "set_version_created",
                actor=actor,
                payload={
                    "set_id": set_id,
                    "set_version": version_number,
                    "catalog_snapshot_id": catalog.snapshot_id,
                    "reason": change_reason,
                    "input_fingerprints": {"content_sha256": content_sha},
                },
            )
            return version

    def set_head(
        self,
        set_id: str,
        state: str,
        version: int,
        expected_head_sha256: str | None,
        change_reason: str,
        *,
        actor: str = "research",
        audit_checker: _AuditChecker | None = None,
    ) -> SetHead:
        self._root(set_id)
        if state not in {"watch", "active", "paused"}:
            raise ValueError(f"invalid set state: {state}")
        with FileLock(self.lock_path):
            current = self._head_from_ledger(set_id)
            if expected_head_sha256 != (self._head_token(current) if current else None):
                raise SetConflictError("set head changed since the request was prepared")
            item = self._load_version(set_id, version)
            self._validate_members(item.members, active_head=(state == "active"))
            if state == "active":
                if audit_checker is None:
                    raise AuditRequiredError("active transition requires an audit checker")
                audit = audit_checker(set_id, version)
                if not audit:
                    raise AuditRequiredError("active transition requires a persisted audit result")
                required = {
                    "integrity": "pass",
                    "incremental": "positive",
                    "parameter_status": "pass",
                    "investability": "paper_ready",
                    "source_completeness": "complete",
                }
                for key, expected in required.items():
                    if audit.get(key) != expected:
                        raise AuditRequiredError(f"active audit gate failed: {key}={audit.get(key)!r}")
            now_head = SetHead(
                set_id=set_id,
                state=state,  # type: ignore[arg-type]
                watch_version=current.watch_version if current else None,
                watch_version_sha256=current.watch_version_sha256 if current else None,
                active_version=version if state == "active" else None,
                active_version_sha256=item.content_sha256 if state == "active" else None,
                paused_version=version if state == "paused" else None,
                paused_version_sha256=item.content_sha256 if state == "paused" else None,
                updated_at=None,
                ledger_record_id=None,
            )
            if state == "watch":
                now_head = now_head.model_copy(update={
                    "watch_version": version,
                    "watch_version_sha256": item.content_sha256,
                })
            record = self.ledger.append(
                "set_head_changed",
                actor=actor,
                payload={
                    "set_id": set_id,
                    "set_version": version,
                    "verdict": state,
                    "reason": change_reason,
                    "input_fingerprints": {"head_sha256": self._head_token(now_head)},
                },
            )
            final_head = now_head.model_copy(update={
                "updated_at": record.recorded_at,
                "ledger_record_id": record.record_id,
            })
            atomic_write_json(self._head_path(set_id), final_head)
            return final_head

    def head(self, set_id: str) -> SetHead | None:
        self._root(set_id)
        head = self._head_from_ledger(set_id)
        if head is not None:
            return head
        path = self._head_path(set_id)
        if not path.exists():
            return None
        return SetHead.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def load_version(self, set_id: str, version: int) -> FactorSetVersion:
        return self._load_version(set_id, version)

    def stale_reasons(self, version: FactorSetVersion) -> list[str]:
        reasons: list[str] = []
        for member in version.members:
            resolved = self.resolver.resolve(member.factor_id)
            if resolved.reference_kind == "dangling_reference":
                reasons.append(f"missing:{member.factor_id}")
            elif resolved.factor_version != member.factor_version:
                reasons.append(f"version_changed:{member.factor_id}")
            elif resolved.definition_fingerprint != member.definition_fingerprint:
                reasons.append(f"definition_changed:{member.factor_id}")
        return reasons

    def list_heads(self) -> list[SetHead]:
        root = self.root / "sets"
        if not root.exists():
            return []
        out: list[SetHead] = []
        for path in sorted(root.iterdir()):
            if path.is_dir():
                head = self.head(path.name)
                if head is not None:
                    out.append(head)
        return out
