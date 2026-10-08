"""HS300 动态筛选 API 测试 — 官方当前成分查询与按当前名单同步日 K。"""
from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import hs300 as hs300_api
from app.hs300.current import CurrentMembers, CurrentMembersError
from app.services import preferences


def _members(symbols: list[str]) -> CurrentMembers:
    return CurrentMembers(
        as_of="2026-09-30",
        source="csindex",
        fetched_at="2026-10-08T12:00:00+08:00",
        members=tuple(
            {"symbol": symbol, "name": f"name-{index}"}
            for index, symbol in enumerate(symbols)
        ),
    )


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
            "symbols": list(symbols),
            "repo": repo,
            "capset": capset,
            "kwargs": kwargs,
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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        hs300_api,
        "load_current_members",
        lambda **_kwargs: _members(["600519.SH", "000001.SZ", "300750.SZ"]),
    )
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
    return TestClient(app), repo, capset


def test_current_returns_official_members(client) -> None:
    http, _, _ = client
    resp = http.get("/api/hs300/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2026-09-30"
    assert body["source"] == "csindex"
    assert body["count"] == 3
    assert [m["symbol"] for m in body["members"]] == ["600519.SH", "000001.SZ", "300750.SZ"]


def test_current_forwards_refresh_flag(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def _loader(**kwargs):
        calls.append(kwargs)
        return _members(["600519.SH"])

    monkeypatch.setattr(hs300_api, "load_current_members", _loader)
    http, _, _ = client
    resp = http.get("/api/hs300/current?refresh=true")
    assert resp.status_code == 200
    assert calls == [{"refresh": True}]


def test_current_fail_closed_without_snapshot_fallback(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs):
        raise CurrentMembersError("官方接口不可用")

    monkeypatch.setattr(hs300_api, "load_current_members", _boom)
    http, _, _ = client
    resp = http.get("/api/hs300/current")
    assert resp.status_code == 503
    assert "官方接口不可用" in resp.json()["detail"]


def test_sync_routes_current_members_without_mutating_global(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, repo, capset = client
    preferences.save({"daily_data_provider": "tickflow"})
    recorder = _SyncRecorder(result=17)
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    resp = http.post(
        "/api/hs300/sync",
        json={"provider": "akshare", "start": "2026-01-01", "end": "2026-09-30"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "akshare"
    assert body["symbols"] == 3
    assert body["rows"] == 17
    assert body["start"] == "2026-01-01"
    assert body["end"] == "2026-09-30"
    assert body["as_of"] == "2026-09-30"
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["symbols"] == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert call["repo"] is repo
    assert call["capset"] is capset
    assert call["kwargs"]["start_date"] == datetime.combine(date(2026, 1, 1), time.min)
    assert call["kwargs"]["end_date"] == datetime.combine(date(2026, 9, 30), time.max)
    assert call["kwargs"]["provider_name"] == "akshare"
    assert call["provider_during_call"] == "tickflow"
    assert call["sync_lock_held"] is False
    assert preferences.get_daily_data_provider() == "tickflow"


def test_sync_recomputes_enriched_for_current_members(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, repo, _ = client
    recorder = _SyncRecorder(result=17)
    pipeline_calls: list[dict] = []
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    monkeypatch.setattr(
        "app.indicators.pipeline.run_pipeline",
        lambda **kwargs: pipeline_calls.append(kwargs) or 17,
    )
    response = http.post(
        "/api/hs300/sync",
        json={"provider": "tickflow", "start": "2026-01-01", "end": "2026-09-30"},
    )
    assert response.status_code == 200
    assert pipeline_calls == [{
        "data_dir": repo.store.data_dir,
        "symbols": ["600519.SH", "000001.SZ", "300750.SZ"],
    }]
    assert repo.cache_calls == ["clear", "refresh"]


def test_sync_fail_closed_when_current_unavailable(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs):
        raise CurrentMembersError("官方接口不可用")

    monkeypatch.setattr(hs300_api, "load_current_members", _boom)
    http, _, _ = client
    resp = http.post("/api/hs300/sync", json={"provider": "akshare"})
    assert resp.status_code == 503
    assert "官方接口不可用" in resp.json()["detail"]


def test_sync_rejects_inverted_range(client) -> None:
    http, _, _ = client
    resp = http.post(
        "/api/hs300/sync",
        json={"provider": "akshare", "start": "2026-09-30", "end": "2026-01-01"},
    )
    assert resp.status_code == 422


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
