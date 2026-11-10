"""HTTP boundary for versioned factor research."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.factor_research.audit import load_audit
from app.factor_research.catalog import load_catalog, load_latest_catalog
from app.factor_research.ledger import LedgerCorruptError, ResearchLedger
from app.factor_research.pools import PoolConflictError, PoolStore
from app.factor_research.references import ReferenceResolver
from app.factor_research.sets import (
    AuditRequiredError,
    FactorSetStore,
    SetConflictError,
)

router = APIRouter(prefix="/api/factor-research", tags=["factor-research"])


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PoolRevisionRequest(_Request):
    expected_revision: int = Field(ge=0)
    members: list[dict[str, Any]] = Field(default_factory=list)
    change_reason: str = Field(min_length=1, max_length=500)
    label: str | None = None
    purpose: str = ""


class SetVersionRequest(_Request):
    expected_head_sha256: str | None = None
    parent_version: int | None = None
    members: list[dict[str, Any]] = Field(default_factory=list)
    change_reason: str = Field(min_length=1, max_length=500)
    scope: dict[str, str] | None = None


class SetHeadRequest(_Request):
    expected_head_sha256: str | None = None
    state: str = Field(pattern="^(watch|active|paused)$")
    version: int = Field(ge=1)
    change_reason: str = Field(min_length=1, max_length=500)
    audit_id: str | None = None


def _data_dir(request: Request) -> Path:
    repo = getattr(request.app.state, "repo", None)
    store = getattr(repo, "store", None)
    configured = getattr(store, "data_dir", None)
    if configured is not None:
        return Path(configured)
    from app.config import settings

    return Path(settings.data_dir)


def _error(status: int, code: str, message: str, **details: Any) -> HTTPException:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return HTTPException(status_code=status, detail=body)


def _resolver(data_dir: Path) -> ReferenceResolver:
    return ReferenceResolver.from_current(data_dir)


def _audit_checker(data_dir: Path, audit_id: str | None):
    if not audit_id:
        return None
    audit = load_audit(data_dir, audit_id)
    if audit is None:
        return None
    gate = audit["manifest"].get("active_gate")
    return gate if isinstance(gate, dict) else None


@router.get("/catalog/latest")
def catalog_latest(request: Request):
    data_dir = _data_dir(request)
    try:
        snapshot = load_latest_catalog(data_dir)
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc
    if snapshot is None:
        raise _error(503, "unavailable", "factor catalog has not been published")
    return snapshot.model_dump(mode="json")


@router.get("/catalog/{snapshot_id}")
def catalog_by_id(snapshot_id: str, request: Request):
    data_dir = _data_dir(request)
    try:
        return load_catalog(data_dir, snapshot_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise _error(404, "not_found", f"catalog snapshot not found: {snapshot_id}") from exc


@router.get("/pools")
def list_pools(request: Request):
    data_dir = _data_dir(request)
    try:
        return {
            "heads": [item.model_dump(mode="json") for item in PoolStore(data_dir).list_heads()],
            "source_completeness": "complete",
        }
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc


@router.get("/pools/{pool_id}")
def get_pool(pool_id: str, request: Request):
    data_dir = _data_dir(request)
    try:
        store = PoolStore(data_dir)
        head = next((item for item in store.list_heads() if item.pool_id == pool_id), None)
        if head is None:
            raise _error(404, "not_found", f"pool not found: {pool_id}")
        revision = store.get(pool_id, head.revision)
        return {
            "head": head.model_dump(mode="json"),
            "revision": revision.model_dump(mode="json") if revision else None,
        }
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc


@router.get("/pools/{pool_id}/revisions/{revision}")
def get_pool_revision(pool_id: str, revision: int, request: Request):
    data_dir = _data_dir(request)
    try:
        item = PoolStore(data_dir).get(pool_id, revision)
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc
    if item is None:
        raise _error(404, "not_found", f"pool revision not found: {pool_id}@{revision}")
    return item.model_dump(mode="json")


@router.post("/pools/{pool_id}/revisions")
def create_pool_revision(pool_id: str, req: PoolRevisionRequest, request: Request):
    data_dir = _data_dir(request)
    try:
        item = PoolStore(data_dir).create_revision(
            pool_id,
            req.members,
            req.expected_revision,
            req.change_reason,
            label=req.label,
            purpose=req.purpose,
        )
    except PoolConflictError as exc:
        raise _error(409, "version_conflict", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise _error(422, "invalid_request", str(exc)) from exc
    return item.model_dump(mode="json")


@router.get("/sets")
def list_sets(request: Request):
    data_dir = _data_dir(request)
    try:
        return {
            "heads": [item.model_dump(mode="json") for item in FactorSetStore(data_dir).list_heads()],
            "source_completeness": "complete",
        }
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc


@router.get("/sets/{set_id}")
def get_set(set_id: str, request: Request):
    data_dir = _data_dir(request)
    try:
        store = FactorSetStore(data_dir)
        head = store.head(set_id)
        if head is None:
            raise _error(404, "not_found", f"set not found: {set_id}")
        version_number = head.active_version or head.watch_version or head.paused_version
        version = store.load_version(set_id, version_number) if version_number else None
        return {
            "head": head.model_dump(mode="json"),
            "version": version.model_dump(mode="json") if version else None,
        }
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc


@router.get("/sets/{set_id}/versions/{version}")
def get_set_version(set_id: str, version: int, request: Request):
    data_dir = _data_dir(request)
    try:
        item = FactorSetStore(data_dir).load_version(set_id, version)
    except FileNotFoundError as exc:
        raise _error(404, "not_found", f"set version not found: {set_id}@{version}") from exc
    except (OSError, ValueError) as exc:
        raise _error(503, "unavailable", str(exc)) from exc
    return item.model_dump(mode="json")


@router.post("/sets/{set_id}/versions")
def create_set_version(set_id: str, req: SetVersionRequest, request: Request):
    data_dir = _data_dir(request)
    try:
        item = FactorSetStore(data_dir).create_version(
            set_id,
            req.members,
            req.parent_version,
            req.expected_head_sha256,
            req.change_reason,
            scope=req.scope,
        )
    except SetConflictError as exc:
        raise _error(409, "version_conflict", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise _error(422, "invalid_request", str(exc)) from exc
    return item.model_dump(mode="json")


@router.post("/sets/{set_id}/head")
def change_set_head(set_id: str, req: SetHeadRequest, request: Request):
    data_dir = _data_dir(request)
    checker = _audit_checker(data_dir, req.audit_id)
    try:
        item = FactorSetStore(data_dir).set_head(
            set_id,
            req.state,
            req.version,
            req.expected_head_sha256,
            req.change_reason,
            audit_checker=checker,
        )
    except SetConflictError as exc:
        raise _error(409, "version_conflict", str(exc)) from exc
    except AuditRequiredError as exc:
        raise _error(422, "audit_required", str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise _error(422, "invalid_request", str(exc)) from exc
    return item.model_dump(mode="json")


@router.get("/ledger")
def read_ledger(
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
):
    data_dir = _data_dir(request)
    try:
        records = ResearchLedger(data_dir).records()
    except LedgerCorruptError as exc:
        raise _error(503, "corrupt_ledger", str(exc)) from exc
    selected = [item for item in records if item.sequence > after_sequence][:limit]
    return {
        "records": [item.model_dump(mode="json") for item in selected],
        "next_after_sequence": selected[-1].sequence if selected else after_sequence,
        "has_more": len(records) > after_sequence + len(selected),
    }


@router.get("/audits/{audit_id}")
def get_audit(audit_id: str, request: Request):
    data_dir = _data_dir(request)
    audit = load_audit(data_dir, audit_id)
    if audit is None:
        raise _error(404, "not_found", f"audit not found: {audit_id}")
    return audit
