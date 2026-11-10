from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.factor_research import router
from app.factor_research.catalog import build_catalog, save_catalog


def _client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


def test_catalog_latest_returns_503_without_snapshot(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/factor-research/catalog/latest")
    assert response.status_code == 503


def test_catalog_latest_and_ledger_are_readable(tmp_path: Path) -> None:
    snapshot = build_catalog(tmp_path, generated_at="2026-11-10T00:00:00Z")
    save_catalog(tmp_path, snapshot)
    client = _client(tmp_path)
    latest = client.get("/api/factor-research/catalog/latest")
    assert latest.status_code == 200
    assert latest.json()["snapshot_id"] == snapshot.snapshot_id
    ledger = client.get("/api/factor-research/ledger")
    assert ledger.status_code == 200
    assert ledger.json()["records"] == []


def test_pool_revision_conflict_is_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = {"expected_revision": 0, "members": [], "change_reason": "init"}
    first = client.post("/api/factor-research/pools/core_v1/revisions", json=payload)
    assert first.status_code == 200
    second = client.post("/api/factor-research/pools/core_v1/revisions", json=payload)
    assert second.status_code == 409
