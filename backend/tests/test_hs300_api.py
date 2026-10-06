"""HS300 API 测试 — 快照/成员查询与同步的 provider 路由、范围校验与恢复。"""
from __future__ import annotations
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import hs300 as hs300_api
from app.services import preferences

def _snapshot(root: Path, year: int, month: int, symbols: list[str]) -> None:
    path = root / f"{year:04d}" / f"{month:02d}" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"Symbol": symbols, "Name": [f"name-{i}" for i in range(len(symbols))]}).write_csv(path)

class _FakeRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self.cache_calls: list[str] = []
    def clear_cache(self) -> None:
        self.cache_calls.append("clear")
    def refresh_cache(self) -> None:
        self.cache_calls.append("refresh")

class _FakeCapabilities:
    pass

class _SyncRecorder:
    def __init__(self, result: int = 42, error: Exception | None = None, zero_row_symbols: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self.result = result
        self.error = error
        self.zero_row_symbols = zero_row_symbols or []
    def __call__(self, symbols, repo, capset, **kwargs):
        self.calls.append({
            "symbols": list(symbols), "repo": repo, "capset": capset, "kwargs": kwargs,
            "provider_during_call": preferences.get_daily_data_provider(),
            "sync_lock_held": hs300_api._SYNC_LOCK.locked(),
        })
        zero_row_out = kwargs.get("zero_row_out")
        if zero_row_out is not None:
            zero_row_out.extend(self.zero_row_symbols)
        if self.error is not None:
            raise self.error
        return self.result

@pytest.fixture()
def snapshot_root(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    _snapshot(root, 2023, 7, ["600519.SS", "000001.SZ"])
    _snapshot(root, 2023, 8, ["600519.SS", "300750.SZ"])
    return root

@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot_root: Path):
    monkeypatch.setattr(hs300_api.settings, "hs300_snapshot_dir", snapshot_root)
    monkeypatch.setattr(hs300_api, "_daily_provider_available", lambda name: name in {"akshare", "tickflow"})
    monkeypatch.setattr(preferences, "_path", lambda: tmp_path / "preferences.json")
    preferences._invalidate_cache()
    repo = _FakeRepo(tmp_path / "store")
    capset = _FakeCapabilities()
    app = FastAPI()
    app.include_router(hs300_api.router)
    app.state.repo = repo
    app.state.capabilities = capset
    monkeypatch.setattr("app.indicators.pipeline.run_pipeline", lambda **_kwargs: 0)
    monkeypatch.setattr("app.api.data.invalidate_storage_cache", lambda: None)
    monkeypatch.setattr("app.jobs.daily_pipeline._refresh_single_view", lambda *args: None)
    http = TestClient(app)
    return http, repo, capset

def test_snapshots_list_available_dates(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    resp = http.get("/api/hs300/snapshots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshots"] == ["2023-07-01", "2023-08-01"]
    assert body["earliest"] == "2023-07-01"
    assert body["latest"] == "2023-08-01"

def test_members_returns_pit_slice_for_query_date(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    resp = http.get("/api/hs300/members?as_of=2023-07-15")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2023-07-15"
    assert body["snapshot_date"] == "2023-07-01"
    symbols = [m["symbol"] for m in body["members"]]
    assert sorted(symbols) == ["000001.SZ", "600519.SH"]

def test_members_between_returns_covering_union(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    resp = http.get("/api/hs300/members-between?start=2023-07-01&end=2023-08-31")
    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == "2023-07-01"
    assert body["end"] == "2023-08-31"
    assert body["snapshot_dates"] == ["2023-07-01", "2023-08-01"]
    symbols = [m["symbol"] for m in body["members"]]
    assert sorted(symbols) == ["000001.SZ", "300750.SZ", "600519.SH"]

def test_sync_routes_to_specified_provider_without_mutating_global(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, repo, capset = client
    preferences.save({"daily_data_provider": "tickflow"})
    recorder = _SyncRecorder(result=17)
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True, "provider": "akshare", "symbols": 3, "rows": 17, "zero_row_symbols": [],
        "start": "2023-07-01", "end": "2023-08-01",
    }
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["symbols"] == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert call["repo"] is repo
    assert call["capset"] is capset
    assert call["kwargs"]["start_date"] == datetime.combine(date(2023, 7, 1), time.min)
    assert call["kwargs"]["end_date"] == datetime.combine(date(2023, 8, 1), time.max)
    assert call["kwargs"]["provider_name"] == "akshare"
    assert call["provider_during_call"] == "tickflow"
    assert call["sync_lock_held"] is False
    assert preferences.get_daily_data_provider() == "tickflow"

def test_sync_recomputes_enriched_for_synced_members(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, repo, _ = client
    recorder = _SyncRecorder(result=17)
    pipeline_calls: list[dict] = []
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    monkeypatch.setattr("app.indicators.pipeline.run_pipeline", lambda **kwargs: pipeline_calls.append(kwargs) or 17)
    response = http.post("/api/hs300/sync", json={"provider": "tickflow"})
    assert response.status_code == 200
    assert pipeline_calls == [{"data_dir": repo.store.data_dir, "symbols": ["600519.SH", "000001.SZ", "300750.SZ"]}]
    assert repo.cache_calls == ["clear", "refresh"]

def test_sync_explicit_range_selects_covering_snapshot(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post("/api/hs300/sync", json={"start": "2023-08-05", "end": "2023-08-20"})
    assert resp.status_code == 200
    assert recorder.calls[0]["symbols"] == ["600519.SH", "300750.SZ"]
    assert resp.json()["start"] == "2023-08-05"
    assert resp.json()["end"] == "2023-08-20"

def test_sync_does_not_mutate_global_provider_when_upstream_raises(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    preferences.save({"daily_data_provider": "tickflow"})
    recorder = _SyncRecorder(error=RuntimeError("akshare boom"))
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    with pytest.raises(RuntimeError, match="akshare boom"):
        http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert preferences.get_daily_data_provider() == "tickflow"
    assert hs300_api._SYNC_IN_PROGRESS is False

def test_sync_rejects_concurrent_request_without_calling_provider(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    monkeypatch.setattr(hs300_api, "_SYNC_IN_PROGRESS", True)
    response = http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert response.status_code == 409
    assert recorder.calls == []

def test_sync_returns_zero_row_symbols_for_partial_results(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder(result=9, zero_row_symbols=["300750.SZ"])
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert resp.status_code == 200
    assert resp.json()["rows"] == 9
    assert resp.json()["zero_row_symbols"] == ["300750.SZ"]

def test_sync_fails_when_provider_returns_no_rows(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder(result=0, zero_row_symbols=["600519.SH", "000001.SZ"])
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert resp.status_code == 502
    assert resp.json()["detail"]["zero_row_symbols"] == ["600519.SH", "000001.SZ"]

def test_sync_rejects_range_before_first_snapshot(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post("/api/hs300/sync", json={"start": "2023-06-01", "end": "2023-07-31"})
    assert resp.status_code == 422
    assert recorder.calls == []

def test_snapshot_dir_resolves_to_configured_path(snapshot_root: Path) -> None:
    from app.config import resolve_hs300_snapshot_dir, hs300_snapshot_default_dir
    assert hs300_api._snapshot_root() == hs300_snapshot_default_dir()
